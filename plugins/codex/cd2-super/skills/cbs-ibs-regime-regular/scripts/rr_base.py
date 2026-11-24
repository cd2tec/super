# Port, feito pela CD2 Tech, do código de ReceitaFederal/calculadora-rtc-regime-regular,
# distribuído sob a licença MIT. Aviso de copyright e licença do original, como estão no LICENSE dele:
#
# MIT License
#
# Copyright (c) 2026 Receita Federal do Brasil
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

"""Leitura da base da Receita para a skill cbs-ibs-regime-regular.

Port das consultas da calculadora oficial do regime regular (calculadora-rtc-regime-regular, tag 1.7.2)
que o caminho de `POST /api/calculadora/regime-geral` e `POST /api/calculadora/observabilidade/regime-geral`
alcança para tpDoc 55, 65 ou ausente, sem NBS e sem gCompraGov (esses ramos a skill recusa antes do motor).
A base é o SQLite `base-receita.db` que o `scripts/receita_base.py` monta das migrações oficiais.

Convenções:
- Cada método espelha um método de repositório Java (pacote br.gov.serpro.rtc.domain.repository) com o
  nome em snake_case; quando dois repositórios usam o mesmo nome (`buscar`, `tem`), o nome ganha a
  entidade. A docstring diz o arquivo e o método Java e de onde vem o SQL: a native query copiada, ou o SQL
  que o Hibernate 6 gera da JPQL (caminho até o id de uma associação usa a coluna da chave estrangeira; os
  demais caminhos viram INNER JOIN).
- O perfil offline abre o SQLite com `date_class=TEXT&date_string_format=yyyy-MM-dd`
  (application-offline.yml): a data vai à consulta como texto `aaaa-mm-dd` e a comparação é de texto, com
  as colunas como estão gravadas (algumas têm hora, `aaaa-mm-dd 00:00:00`). Aqui é igual: o parâmetro é
  `date.isoformat()` e o SQL roda no próprio SQLite.
- Linhas saem como `dict` com os nomes dos campos das entidades e DTOs Java. Datas são `datetime.date`,
  booleanos são `bool` e enums são o nome do enum (texto).
- BigDecimal é `decimal.Decimal` do texto do número. O sqlite-jdbc (getBigDecimal) lê a coluna como texto,
  que o próprio SQLite escreve (um REAL sai com até 15 dígitos significativos: 0.30000000000000004 vira
  "0.3"), e faz `new BigDecimal(texto)`. Aqui a consulta pede `CAST(coluna AS TEXT)` e o Python faz
  `Decimal(texto)`: o mesmo texto, do mesmo SQLite, por construção (tipo "n" do mapa). A exceção é a native
  query de AliquotaAdValoremServicoRepository.buscarAliquotaAdValoremPorClassificacaoTributaria: nela o
  Hibernate lê a coluna pelo tipo do metadado JDBC (ponto flutuante, não texto) e o Spring converte o
  número para BigDecimal pelo texto dele (`toString`, o decimal mais curto). Ali a coluna vem como REAL e
  o Python faz `Decimal(repr(valor))` (tipo "f"). Hoje os dois caminhos dão o mesmo número em toda a base.
- "Nenhuma linha" é `None` (o `null` ou o `Optional.empty()` do Java). Consulta que no Java devolve um
  resultado só e acha mais de um levanta `ResultadoNaoUnico` (IncorrectResultSizeDataAccessException).
- Sem cache além de um dicionário por instância. A base abre somente leitura. Só biblioteca padrão.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

NOME_BASE = "base-receita.db"

# NomenclaturaService.EXCEPTION_CLASSIFICACOES: classificações de anexo misto (NCM e NBS) em que o NCM não é
# locação de bens; NF-e/NFC-e aceitam só NCM e NFS-e só NBS (Anexo IX da LC 214/2025: 200038 e 515001).
EXCEPTION_CLASSIFICACOES = frozenset({"200038", "515001"})

# SiglasDFeEnum: nome do enum -> sigla.
SIGLAS_DFE = {
    "NFEABI": "NFe ABI", "NFE": "NFe", "NFCE": "NFCe", "CTE": "CTe", "CTEOS": "CTe OS",
    "CTESIMPLIFICADO": "CT-e Simplificado", "BPE": "BPe", "BPETA": "BPe TA", "BPETM": "BPe TM",
    "NF3E": "NF3e", "NFSE": "NFSe", "NFSEVIA": "NFSe Via", "NFCOM": "NFCom", "DERE": "DERE",
    "DIR": "DIR", "DUIMP": "Duimp", "NFAG": "NFAg", "NFGAS": "NFGas",
}
_NCM_APLICAVEL = frozenset({"NFSE", "NFE", "NFCE"})  # SiglasDFeEnum.NCM_APLICAVEL
_NBS_APLICAVEL = frozenset({"NFSE"})  # SiglasDFeEnum.NBS_APLICAVEL
_IMPOSTO_SELETIVO_APLICAVEL = frozenset({"NFE", "NFCE"})  # SiglasDFeEnum.IMPOSTO_SELETIVO_APLICAVEL

# TributoEnum: código do tributo (TBTO_ID).
TRIBUTO_IS = 1
TRIBUTO_CBS = 2
TRIBUTO_IBS_ESTADUAL = 3
TRIBUTO_IBS_MUNICIPAL = 4


# --- Erros --------------------------------------------------------------------------------

class BaseIndisponivel(Exception):
    """A base não foi achada, não abriu ou não é a base da Receita."""


class ResultadoNaoUnico(Exception):
    """IncorrectResultSizeDataAccessException: a consulta devia trazer uma linha só."""


class ValidacaoException(Exception):
    """ValidacaoException.java."""


class EntidadeNaoEncontradaException(Exception):
    """EntidadeNaoEncontradaException.java."""


class SiglaDFeNaoEncontradaException(ValidacaoException):
    def __init__(self, sigla):
        super().__init__(f"Sigla DFe inválida: {sigla}")


class ClassificacaoTributariaNaoEncontradaException(EntidadeNaoEncontradaException):
    """Os dois construtores do Java: (codigo, tributo, data) ou (id)."""

    def __init__(self, codigo=None, tributo=None, data=None, *, id_classificacao=None):
        if id_classificacao is not None:
            super().__init__(f"Classificação tributária não encontrada para id {id_classificacao}")
        else:
            super().__init__(f"Classificação tributária não encontrada para código {codigo} e data "
                             f"{data} ({tributo})")


# --- Mapeamento coluna -> campo -----------------------------------------------------------
# Tipos: s texto, i inteiro, b Boolean (nulo fica None), B boolean primitivo (nulo vira False), d data,
# n Decimal do texto que o SQLite escreve (a consulta pede CAST(coluna AS TEXT), como o getBigDecimal do
# sqlite-jdbc) e f Decimal do número de ponto flutuante (só a native query, veja a docstring do módulo).

_TIPO_DFE = (  # entity/TipoDfe.java
    ("id", "TPDF_ID", "i"), ("tipo", "TPDF_TIPO", "i"), ("sigla", "TPDF_SIGLA", "s"),
    ("descricao", "TPDF_DESCRICAO", "s"), ("inicioVigencia", "TPDF_INICIO_VIGENCIA", "d"),
    ("fimVigencia", "TPDF_FIM_VIGENCIA", "d"), ("permiteNcmZero", "TPDF_B_PERMITE_NCM_ZERO", "B"),
    ("possuiTratamento", "TPDF_B_POSSUI_TRATAMENTO_CALCULADORA", "B"),
)
_SITUACAO_TRIBUTARIA = (  # entity/SituacaoTributaria.java
    ("id", "SITR_ID", "i"), ("codigo", "SITR_CD", "s"), ("descricao", "SITR_DESCRICAO", "s"),
    ("inGrupoIbsCbs", "SITR_IND_GIBSCBS", "b"), ("inGrupoIbsCbsMono", "SITR_IND_GIBSCBSMONO", "b"),
    ("inGrupoReducao", "SITR_IND_GRED", "b"), ("inGrupoDiferimento", "SITR_IND_GDIF", "b"),
    ("inGrupoTranferenciaCredito", "SITR_IND_GTRANSFCRED", "b"),
    ("inGrupoCreditoPresumidoIbsZfm", "SITR_IND_GCREDPRESIBSZFM", "b"),
    ("inGrupoAjusteCompetencia", "SITR_IND_GAJUSTECOMPET", "b"),
    ("inicioVigencia", "SITR_INICIO_VIGENCIA", "d"), ("fimVigencia", "SITR_FIM_VIGENCIA", "d"),
)
_TRATAMENTO_TRIBUTARIO = (  # entity/TratamentoTributario.java
    ("id", "TRTR_ID", "i"), ("descricao", "TRTR_DESCRICAO", "s"),
    ("expressaoAliquota", "TRTR_EXPRESSAO_ALIQUOTA", "s"),
    ("expressaoAliquotaEfetiva", "TRTR_EXPRESSAO_ALIQUOTA_EFETIVA", "s"),
    ("expressaoBaseCalculo", "TRTR_EXPRESSAO_BASE_CALCULO", "s"),
    ("expressaoTributoCalculado", "TRTR_EXPRESSAO_TRIBUTO_CALCULADO", "s"),
    ("expressaoTributoDevido", "TRTR_EXPRESSAO_TRIBUTO_DEVIDO", "s"),
    ("expressaoPercentualDiferimento", "TRTR_EXPRESSAO_PERCENTUAL_DIFERIMENTO", "s"),
    ("expressaoValorDiferimento", "TRTR_EXPRESSAO_VALOR_DIFERIMENTO", "s"),
    ("inIncompativelComSuspensao", "TRTR_IN_INCOMPATIVEL_COM_SUSPENSAO", "B"),
    ("inExigeGrupoDesoneracao", "TRTR_IN_EXIGE_GRUPO_DESONERACAO", "B"),
    ("inPossuiPercentualReducao", "TRTR_IN_POSSUI_PERCENTUAL_REDUCAO", "B"),
    ("inPossuiAjuste", "TRTR_IN_POSSUI_AJUSTE", "B"), ("inPossuiRedutor", "TRTR_IN_POSSUI_REDUTOR", "B"),
    ("inPossuiMonofasia", "TRTR_IN_POSSUI_MONOFASIA", "B"),
    ("inicioVigencia", "TRTR_INICIO_VIGENCIA", "d"), ("fimVigencia", "TRTR_FIM_VIGENCIA", "d"),
)
_CLASSIFICACAO_CALCULO = (  # dto/ClassificacaoTributariaCalculoDTO.java
    ("id", "ct.CLTR_ID", "i"), ("codigo", "ct.CLTR_CD", "s"), ("tipoAliquota", "ct.CLTR_TIPO_ALIQUOTA", "s"),
    ("inGrupoMonofasiaPadrao", "ct.CLTR_IND_GMONOPADRAO", "b"),
    ("inGrupoMonofasiaReten", "ct.CLTR_IND_GMONORETEN", "b"),
    ("inGrupoMonofasiaRet", "ct.CLTR_IND_GMONORET", "b"),
    ("inGrupoMonofasiaDiferimento", "ct.CLTR_IND_GMONODIF", "b"),
    ("inGrupoReducao", "st.SITR_IND_GRED", "b"),
)


def _converter(valor, tipo):
    if tipo == "B":  # boolean primitivo da entidade: nulo vira false
        return bool(valor)
    if valor is None:
        return None
    if tipo == "b":
        return bool(valor)
    if tipo == "d":
        return date.fromisoformat(str(valor)[:10])
    if tipo == "n":
        if not isinstance(valor, str):  # sem o CAST AS TEXT a conta deixaria de ser a do sqlite-jdbc
            raise TypeError(f"coluna BigDecimal tem de vir como texto (CAST AS TEXT), veio {type(valor).__name__}")
        return Decimal(valor)
    if tipo == "f":
        return Decimal(repr(valor)) if isinstance(valor, float) else Decimal(str(valor))
    return valor


def _colunas(mapa, alias=None):
    return ", ".join(coluna if alias is None else f"{alias}.{coluna}" for _, coluna, _ in mapa)


def _linha(mapa, valores):
    """Monta o dict de uma entidade a partir de uma fatia de valores, na ordem do mapa."""
    return {campo: _converter(valor, tipo) for (campo, _, tipo), valor in zip(mapa, valores)}


def _iso(data) -> str:
    """A data como o perfil offline a manda ao SQLite (yyyy-MM-dd)."""
    if isinstance(data, datetime):
        data = data.date()
    if not isinstance(data, date):
        raise TypeError(f"data deve ser datetime.date, veio {type(data).__name__}")
    return data.isoformat()


# --- Siglas de DF-e (SiglasDFeEnum) ---------------------------------------------------------

def normalizar_sigla(sigla) -> str:
    """SiglasDFeEnum.normalizarSigla: maiúsculas, sem espaço (\\s do Java), hífen nem sublinhado."""
    if sigla is None:
        return ""
    return re.sub(r"[ \t\n\x0b\f\r\-_]", "", sigla.upper())


def sigla_dfe_por_normalizada(sigla) -> str:
    """SiglasDFeEnum.getPorSiglaNormalizada: devolve o nome do enum ou levanta a exceção."""
    normalizada = normalizar_sigla(sigla)
    if normalizada in SIGLAS_DFE:
        return normalizada
    raise SiglaDFeNaoEncontradaException(sigla)


def ncm_aplicavel(sigla) -> bool:
    """SiglasDFeEnum.ncmAplicavel."""
    return normalizar_sigla(sigla) in _NCM_APLICAVEL


def nbs_aplicavel(sigla) -> bool:
    """SiglasDFeEnum.nbsAplicavel."""
    return normalizar_sigla(sigla) in _NBS_APLICAVEL


def imposto_seletivo_aplicavel(sigla) -> bool:
    """SiglasDFeEnum.impostoSeletivoAplicavel."""
    return normalizar_sigla(sigla) in _IMPOSTO_SELETIVO_APLICAVEL


# --- Base ---------------------------------------------------------------------------------

class Base:
    """Leitura somente leitura da base da Receita."""

    def __init__(self, caminho=None):
        if caminho is None:
            caminho = self._achar_base()
        caminho = Path(caminho)
        if not caminho.is_file():
            raise BaseIndisponivel(f"Base da Receita não encontrada: {caminho}")
        self.caminho = caminho
        try:
            self._con = sqlite3.connect(f"{caminho.resolve().as_uri()}?mode=ro", uri=True)
            linha = self._con.execute("SELECT valor FROM CD2_META WHERE chave = 'meta'").fetchone()
            if linha is None:
                raise BaseIndisponivel(f"A base {caminho} não tem a linha 'meta' em CD2_META.")
            self._meta = json.loads(linha[0])
        except BaseIndisponivel:
            self.fechar()
            raise
        except (sqlite3.Error, ValueError) as erro:
            self.fechar()
            raise BaseIndisponivel(f"Não foi possível abrir a base {caminho}: {erro}") from erro
        self._cache = {}

    @staticmethod
    def _achar_base() -> Path:
        aqui = Path(__file__).resolve().parent
        candidatos = (aqui.parent / "references" / NOME_BASE, aqui / NOME_BASE)
        for candidato in candidatos:
            if candidato.is_file():
                return candidato
        raise BaseIndisponivel(
            "Base da Receita não encontrada. Procurei em: " + ", ".join(str(c) for c in candidatos))

    def fechar(self):
        con = getattr(self, "_con", None)
        if con is not None:
            con.close()
            self._con = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.fechar()

    # --- acesso -----------------------------------------------------------------------

    def _consultar(self, sql: str, **parametros):
        """Roda a consulta e guarda as linhas (tuplas, imutáveis) no dicionário da instância."""
        chave = (sql, tuple(sorted(parametros.items())))
        if chave not in self._cache:
            try:
                cursor = self._con.execute(sql, parametros)
                self._cache[chave] = (tuple(c[0] for c in cursor.description), tuple(cursor.fetchall()))
            except sqlite3.DatabaseError as erro:  # página danificada, arquivo cortado, tabela que sumiu
                raise BaseIndisponivel(
                    f"A base {self.caminho} está danificada ou incompleta: {erro}. "
                    "Baixe a skill de novo para recuperar o arquivo.") from erro
        return self._cache[chave]

    def _linhas(self, mapa, sql, **parametros):
        return [_linha(mapa, valores) for valores in self._consultar(sql, **parametros)[1]]

    def _primeiro(self, mapa, sql, **parametros):
        linhas = self._linhas(mapa, sql, **parametros)
        return linhas[0] if linhas else None

    def _contar(self, sql, **parametros) -> int:
        return self._consultar(sql, **parametros)[1][0][0]

    def _existe(self, sql, **parametros) -> bool:
        return bool(self._consultar(sql, **parametros)[1][0][0])

    def _unico(self, descricao, sql, **parametros):
        """getSingleResult do Spring Data: nenhuma linha é None, mais de uma é ResultadoNaoUnico."""
        linhas = self._consultar(sql, **parametros)[1]
        if len(linhas) > 1:
            raise ResultadoNaoUnico(f"{descricao}: esperava no máximo 1 linha, vieram {len(linhas)}")
        return linhas[0] if linhas else None

    # --- metadados --------------------------------------------------------------------

    def meta(self) -> dict:
        """CD2_META, chave 'meta' (mesmo conteúdo de ler_meta em scripts/receita_base.py)."""
        return json.loads(json.dumps(self._meta))

    def versao_base(self) -> dict:
        """VersaoBaseDadosRepository.findTopByOrderByIdDesc (via VersaoBaseDadosService.getUltimaVersao).

        Consulta derivada do nome do método: ORDER BY id DESC com limite 1. Devolve {versao, descricao, data}.
        """
        linhas = self._consultar(
            "SELECT VRBD_VERSAO_BASE_DADO, VRBD_DESCRICAO, VRBD_DATA FROM VERSAO_BASE_DADO "
            "ORDER BY VRBD_ID DESC LIMIT 1")[1]
        if not linhas:
            raise BaseIndisponivel("Nenhuma versão de base de dados encontrada")
        versao, descricao, data = linhas[0]
        return {"versao": versao, "descricao": descricao, "data": _converter(data, "d")}

    def cabecalhos_versao(self) -> dict:
        """VersaoAplicacaoService.getHeaders: cabeçalhos X-CALC-* que a oficial manda no 200."""
        return {"X-CALC-APP-VERSION": self._meta["calculadora"]["versao"],
                "X-CALC-DB-VERSION": self.versao_base()["versao"]}

    # --- município e UF -----------------------------------------------------------------

    def buscar_uf_por_municipio(self, municipio: int) -> str | None:
        """MunicipioService.buscarUfPorMunicipio = MunicipioRepository.findById(codigo).getUf().getSigla().

        SQL do findById: a entidade Municipio com o @ManyToOne Uf ansioso (LEFT JOIN). Sem município, None
        (o Java levanta MunicipioNaoEncontradoException, que é do motor).
        """
        linha = self._unico(
            f"Município {municipio}",
            "SELECT u.UF_SIGLA FROM MUNICIPIO m LEFT JOIN UF u ON u.UF_CD = m.MUNI_UF_CD WHERE m.MUNI_CD = :codigo",
            codigo=municipio)
        return None if linha is None else linha[0]

    def validar_municipio(self, codigo_municipio: int, sigla_uf: str) -> int:
        """MunicipioRepository.validarMunicipio: 0 válido, 1 município não existe, 2 é de outra UF.

        SQL gerado da JPQL: `m.uf.sigla` é caminho além do id, então vira INNER JOIN com UF, e `COUNT(m)`
        conta a chave do município.
        """
        return self._contar(
            "SELECT CASE WHEN COUNT(m.MUNI_CD) = 0 THEN 1 "
            "WHEN COUNT(m.MUNI_CD) > 0 AND LOWER(u.UF_SIGLA) != LOWER(:sigla) THEN 2 ELSE 0 END "
            "FROM MUNICIPIO m JOIN UF u ON u.UF_CD = m.MUNI_UF_CD WHERE m.MUNI_CD = :codigo",
            codigo=codigo_municipio, sigla=sigla_uf)

    def consultar_por_sigla(self, sigla) -> int | None:
        """UfRepository.consultarPorSigla (via UfService.buscar). SQL gerado da JPQL."""
        linha = self._unico(f"UF {sigla}", "SELECT u.UF_CD FROM UF u WHERE u.UF_SIGLA = UPPER(:sigla)",
                            sigla=sigla)
        return None if linha is None else linha[0]

    def existe_uf(self, sigla) -> bool:
        """UfRepository.existeUf (via UfService.validarUf). SQL gerado da JPQL."""
        return self._existe("SELECT EXISTS (SELECT 1 FROM UF u WHERE u.UF_SIGLA = UPPER(:sigla))", sigla=sigla)

    # --- tipo de documento --------------------------------------------------------------

    def buscar_por_tipo(self, tipo: int, data: date) -> dict | None:
        """TipoDfeRepository.buscarPorTipo (via TipoDfeService.buscarPorTipo). SQL gerado da JPQL.

        Devolve a entidade TipoDfe; o service levanta TipoDfeNaoEncontradoException sem linha e
        TipoDfeNaoTratadoException sem `possuiTratamento` (as duas são do motor).
        """
        linha = self._unico(
            f"TipoDfe {tipo}",
            f"SELECT {_colunas(_TIPO_DFE)} FROM TIPO_DFE WHERE TPDF_TIPO = :tipo "
            "AND :data BETWEEN TPDF_INICIO_VIGENCIA AND COALESCE(TPDF_FIM_VIGENCIA, :data)",
            tipo=tipo, data=_iso(data))
        return None if linha is None else _linha(_TIPO_DFE, linha)

    def buscar_tipo_dfe_classificacao(self, id_classificacao: int, data: date) -> list:
        """TipoDfeClassificacaoRepository.buscar (via TipoDfeClassificacaoService.buscar e validarVinculo).

        SQL gerado da JPQL `FROM TipoDfeClassificacao WHERE ...`; o TipoDfe (@ManyToOne ansioso) vem por JOIN.
        Cada item traz {id, classificacaoTributariaId, inicioVigencia, fimVigencia, tipoDfe}.
        """
        sql = (f"SELECT t.TDCL_ID, t.TDCL_CLTR_ID, t.TDCL_INICIO_VIGENCIA, t.TDCL_FIM_VIGENCIA, "
               f"{_colunas(_TIPO_DFE, 'td')} FROM TIPO_DFE_CLASSIFICACAO t "
               "JOIN TIPO_DFE td ON td.TPDF_ID = t.TDCL_TPDF_ID "
               "WHERE t.TDCL_CLTR_ID = :id AND (t.TDCL_INICIO_VIGENCIA <= :data "
               "AND (t.TDCL_FIM_VIGENCIA IS NULL OR t.TDCL_FIM_VIGENCIA >= :data))")
        resultado = []
        for v in self._consultar(sql, id=id_classificacao, data=_iso(data))[1]:
            resultado.append({
                "id": v[0], "classificacaoTributariaId": v[1],
                "inicioVigencia": _converter(v[2], "d"), "fimVigencia": _converter(v[3], "d"),
                "tipoDfe": _linha(_TIPO_DFE, v[4:])})
        return resultado

    # --- situação e classificação tributária --------------------------------------------

    def existe_cst(self, cst: str, id_tributo: int, data: date) -> bool:
        """SituacaoTributariaRepository.existeCst (via SituacaoTributariaService.validarCst).

        SQL gerado da JPQL: `t.situacaoTributaria.codigo` vira INNER JOIN com SITUACAO_TRIBUTARIA.
        """
        return self._existe(
            "SELECT EXISTS (SELECT 1 FROM TRIBUTO_SITUACAO_TRIBUTARIA t "
            "JOIN SITUACAO_TRIBUTARIA s ON s.SITR_ID = t.TRST_SITR_ID "
            "WHERE t.TRST_TBTO_ID = :id_tributo AND s.SITR_CD = :cst "
            "AND :data BETWEEN t.TRST_INICIO_VIGENCIA AND COALESCE(t.TRST_FIM_VIGENCIA, :data) "
            "AND :data BETWEEN s.SITR_INICIO_VIGENCIA AND COALESCE(s.SITR_FIM_VIGENCIA, :data))",
            cst=cst, id_tributo=id_tributo, data=_iso(data))

    def consultar_situacao_tributaria_por_codigo(self, cst, id_tributo, data: date) -> dict | None:
        """SituacaoTributariaRepository.consultarSituacaoTributariaPorCodigo (native query, LIMIT 1).

        O service passa a data como `data.toString()`, o mesmo texto aaaa-mm-dd.
        """
        return self._primeiro(
            _SITUACAO_TRIBUTARIA,
            f"SELECT {_colunas(_SITUACAO_TRIBUTARIA, 's')} FROM SITUACAO_TRIBUTARIA s "
            "JOIN TRIBUTO_SITUACAO_TRIBUTARIA tst ON tst.TRST_SITR_ID = s.SITR_ID "
            "JOIN TRIBUTO t ON t.TBTO_ID = tst.TRST_TBTO_ID "
            "WHERE COALESCE(s.SITR_CD, '') = COALESCE(:cst, '') "
            "AND COALESCE(t.TBTO_ID, 0) = COALESCE(:id_tributo, 0) "
            "AND COALESCE(:data, '') BETWEEN COALESCE(tst.TRST_INICIO_VIGENCIA, '') "
            "AND COALESCE(tst.TRST_FIM_VIGENCIA, :data) "
            "AND COALESCE(:data, '') BETWEEN COALESCE(s.SITR_INICIO_VIGENCIA, '') "
            "AND COALESCE(s.SITR_FIM_VIGENCIA, :data) "
            "AND COALESCE(:data, '') BETWEEN COALESCE(t.TBTO_INICIO_VIGENCIA, '') "
            "AND COALESCE(t.TBTO_FIM_VIGENCIA, :data) "
            "LIMIT 1",
            cst=cst, id_tributo=id_tributo, data=_iso(data))

    def buscar_classificacao_tributaria(self, codigo: str, id_tributo: int, data: date) -> dict | None:
        """ClassificacaoTributariaRepository.buscarClassificacaoTributaria (native query).

        Via ClassificacaoTributariaService.buscarClassificacaoTributariaCbsIbs (tributo 2) e
        ...ImpostoSeletivo (tributo 1). Devolve o ClassificacaoTributariaDTO {id, codigo, nomenclatura, cst}.
        """
        linha = self._unico(
            f"Classificação tributária {codigo} (tributo {id_tributo}) em {_iso(data)}",
            "SELECT ct.CLTR_ID, ct.CLTR_CD, ct.CLTR_NOMENCLATURA, st.SITR_CD "
            "FROM CLASSIFICACAO_TRIBUTARIA ct "
            "JOIN SITUACAO_TRIBUTARIA st ON ct.CLTR_SITR_ID = st.SITR_ID "
            "JOIN TRIBUTO_SITUACAO_TRIBUTARIA tst ON tst.TRST_SITR_ID = st.SITR_ID "
            "JOIN TRIBUTO tb ON tst.TRST_TBTO_ID = tb.TBTO_ID "
            "WHERE ct.CLTR_CD = :codigo AND tb.TBTO_ID = :id_tributo "
            "AND :data BETWEEN ct.CLTR_INICIO_VIGENCIA AND COALESCE(ct.CLTR_FIM_VIGENCIA, :data) "
            "AND :data BETWEEN st.SITR_INICIO_VIGENCIA AND COALESCE(st.SITR_FIM_VIGENCIA, :data) "
            "AND :data BETWEEN tst.TRST_INICIO_VIGENCIA AND COALESCE(tst.TRST_FIM_VIGENCIA, :data) "
            "AND :data BETWEEN tb.TBTO_INICIO_VIGENCIA AND COALESCE(tb.TBTO_FIM_VIGENCIA, :data)",
            codigo=codigo, id_tributo=id_tributo, data=_iso(data))
        if linha is None:
            return None
        i, cd, nomenclatura, cst = linha
        return {"id": i, "codigo": cd, "nomenclatura": nomenclatura, "cst": cst}

    def buscar_classificacao_tributaria_calculo(self, id_classificacao: int) -> dict | None:
        """ClassificacaoTributariaRepository.buscarClassificacaoTributariaCalculo.

        SQL gerado da JPQL: `ct.situacaoTributaria.inGrupoReducao` vira INNER JOIN com SITUACAO_TRIBUTARIA.
        Devolve o ClassificacaoTributariaCalculoDTO. Sem vigência: busca pelo id.
        """
        linha = self._unico(
            f"Classificação tributária id {id_classificacao}",
            f"SELECT {_colunas(_CLASSIFICACAO_CALCULO)} FROM CLASSIFICACAO_TRIBUTARIA ct "
            "JOIN SITUACAO_TRIBUTARIA st ON st.SITR_ID = ct.CLTR_SITR_ID WHERE ct.CLTR_ID = :id",
            id=id_classificacao)
        return None if linha is None else _linha(_CLASSIFICACAO_CALCULO, linha)

    def buscar_tratamento_classificacao(self, id_classificacao: int, data: date) -> dict | None:
        """TratamentoClassificacaoRepository.buscarTratamentoClassificacao.

        SQL gerado da JPQL: os ids saem das chaves estrangeiras e os indicadores pedem INNER JOIN com
        TRATAMENTO_TRIBUTARIO. Devolve o TratamentoClassificacaoDTO.
        """
        linha = self._unico(
            f"Tratamento da classificação {id_classificacao} em {_iso(data)}",
            "SELECT t.TRCL_TRTR_ID, t.TRCL_CLTR_ID, tt.TRTR_IN_INCOMPATIVEL_COM_SUSPENSAO, "
            "tt.TRTR_IN_EXIGE_GRUPO_DESONERACAO FROM TRATAMENTO_CLASSIFICACAO t "
            "JOIN TRATAMENTO_TRIBUTARIO tt ON tt.TRTR_ID = t.TRCL_TRTR_ID "
            "WHERE t.TRCL_CLTR_ID = :id AND (t.TRCL_INICIO_VIGENCIA <= :data "
            "AND (t.TRCL_FIM_VIGENCIA IS NULL OR t.TRCL_FIM_VIGENCIA >= :data))",
            id=id_classificacao, data=_iso(data))
        if linha is None:
            return None
        return {"idTratamentoTributario": linha[0], "idClassificacaoTributaria": linha[1],
                "inIncompativelComSuspensao": bool(linha[2]), "inExigeGrupoDesoneracao": bool(linha[3])}

    def buscar_tratamento_tributario(self, id_tratamento: int) -> dict | None:
        """TratamentoTributarioRepository.findById (via TratamentoTributarioService.buscar).

        Consulta derivada do JpaRepository: a entidade pelo id, sem vigência.
        """
        return self._primeiro(
            _TRATAMENTO_TRIBUTARIO,
            f"SELECT {_colunas(_TRATAMENTO_TRIBUTARIO)} FROM TRATAMENTO_TRIBUTARIO WHERE TRTR_ID = :id",
            id=id_tratamento)

    def buscar_fundamentacao_classificacao(self, id_classificacao: int, data: date) -> dict | None:
        """FundamentacaoClassificacaoRepository.buscar (via FundamentacaoClassificacaoService.buscar).

        SQL gerado da JPQL: `fc.fundamentacaoLegal.textoCurto` e `fc.classificacaoTributaria.memoriaCalculo`
        viram INNER JOIN. Devolve o FundamentacaoClassificacaoDTO {textoCurto, memoriaCalculo}.
        """
        linha = self._unico(
            f"Fundamentação da classificação {id_classificacao} em {_iso(data)}",
            "SELECT fl.FDLG_TEXTO_CURTO, ct.CLTR_MEMORIA_CALCULO FROM FUNDAMENTACAO_CLASSIFICACAO fc "
            "JOIN FUNDAMENTACAO_LEGAL fl ON fl.FDLG_ID = fc.FDCL_FDLG_ID "
            "JOIN CLASSIFICACAO_TRIBUTARIA ct ON ct.CLTR_ID = fc.FDCL_CLTR_ID "
            "WHERE fc.FDCL_CLTR_ID = :id "
            "AND :data BETWEEN fc.FDCL_INICIO_VIGENCIA AND COALESCE(fc.FDCL_FIM_VIGENCIA, :data)",
            id=id_classificacao, data=_iso(data))
        return None if linha is None else {"textoCurto": linha[0], "memoriaCalculo": linha[1]}

    def buscar_percentual_reducao(self, id_classificacao: int, id_tributo: int, data: date) -> Decimal | None:
        """PercentualReducaoRepository.buscar (via PercentualReducaoService.buscar). SQL gerado da JPQL."""
        linha = self._unico(
            f"Percentual de redução da classificação {id_classificacao}, tributo {id_tributo}, em {_iso(data)}",
            "SELECT CAST(PERE_VALOR AS TEXT) FROM PERCENTUAL_REDUCAO "
            "WHERE PERE_CLTR_ID = :id AND PERE_TBTO_ID = :id_tributo "
            "AND (PERE_INICIO_VIGENCIA <= :data AND (PERE_FIM_VIGENCIA IS NULL OR PERE_FIM_VIGENCIA >= :data))",
            id=id_classificacao, id_tributo=id_tributo, data=_iso(data))
        return None if linha is None else _converter(linha[0], "n")

    # --- NCM e anexos -------------------------------------------------------------------

    def existe_ncm(self, ncm: str, data: date) -> bool:
        """NcmRepository.existeNcm (via NcmService.existeNcm). SQL gerado da JPQL."""
        return self._existe(
            "SELECT EXISTS (SELECT 1 FROM NCM n WHERE n.NCM_CD = :codigo "
            "AND :data BETWEEN n.NCM_INICIO_VIGENCIA AND COALESCE(n.NCM_FIM_VIGENCIA, :data))",
            codigo=ncm, data=_iso(data))

    def tem_ncm_aplicavel_para_classificacao(self, id_classificacao: int, data: date) -> bool:
        """NcmAplicavelRepository.tem (NcmAplicavelService e NomenclaturaService). SQL gerado da JPQL."""
        return self._existe(
            "SELECT EXISTS (SELECT 1 FROM NCM_APLICAVEL n WHERE n.NCMA_CLTR_ID = :id "
            "AND :data BETWEEN n.NCMA_INICIO_VIGENCIA AND COALESCE(n.NCMA_FIM_VIGENCIA, :data))",
            id=id_classificacao, data=_iso(data))

    def tem_ncm_aplicavel(self, ncm: str, id_classificacao: int, data: date) -> bool:
        """NcmAplicavelRepository.temNcmAplicavel: o código cadastrado é prefixo do informado. JPQL."""
        return self._existe(
            "SELECT EXISTS (SELECT 1 FROM NCM_APLICAVEL n "
            "WHERE n.NCMA_NCM_CD = SUBSTR(:ncm, 1, LENGTH(n.NCMA_NCM_CD)) AND n.NCMA_CLTR_ID = :id "
            "AND :data BETWEEN n.NCMA_INICIO_VIGENCIA AND COALESCE(n.NCMA_FIM_VIGENCIA, :data))",
            ncm=ncm, id=id_classificacao, data=_iso(data))

    def tem_excecao_ncm_aplicavel(self, ncm: str, id_classificacao: int, data: date) -> bool:
        """NcmAplicavelRepository.temExcecaoNcmAplicavel. SQL gerado da JPQL (JOIN e.ncmAplicavel)."""
        return self._existe(
            "SELECT EXISTS (SELECT 1 FROM EXCECAO_NCM_APLICAVEL e "
            "JOIN NCM_APLICAVEL n ON n.NCMA_ID = e.ENCM_NCMA_ID "
            "WHERE n.NCMA_NCM_CD = SUBSTR(:ncm, 1, LENGTH(n.NCMA_NCM_CD)) "
            "AND e.ENCM_NCM_CD = SUBSTR(:ncm, 1, LENGTH(e.ENCM_NCM_CD)) AND n.NCMA_CLTR_ID = :id "
            "AND :data BETWEEN e.ENCM_INICIO_VIGENCIA AND COALESCE(e.ENCM_FIM_VIGENCIA, :data))",
            ncm=ncm, id=id_classificacao, data=_iso(data))

    def tem_ncm_aplicavel_sem_excecao(self, ncm: str, id_classificacao: int, data: date) -> bool:
        """NcmAplicavelRepository.temNcmAplicavelSemExcecao. SQL gerado da JPQL (LEFT JOIN da exceção)."""
        return self._existe(
            "SELECT EXISTS (SELECT 1 FROM NCM_APLICAVEL n "
            "LEFT JOIN EXCECAO_NCM_APLICAVEL e ON e.ENCM_NCMA_ID = n.NCMA_ID "
            "WHERE n.NCMA_NCM_CD = SUBSTR(:ncm, 1, LENGTH(n.NCMA_NCM_CD)) AND n.NCMA_CLTR_ID = :id "
            "AND :data BETWEEN n.NCMA_INICIO_VIGENCIA AND COALESCE(n.NCMA_FIM_VIGENCIA, :data) "
            "AND (e.ENCM_ID IS NULL "
            "OR :data NOT BETWEEN e.ENCM_INICIO_VIGENCIA AND COALESCE(e.ENCM_FIM_VIGENCIA, :data)))",
            ncm=ncm, id=id_classificacao, data=_iso(data))

    def tem_nbs_aplicavel_para_classificacao(self, id_classificacao: int, data: date) -> bool:
        """NbsAplicavelRepository.tem. SQL gerado da JPQL.

        No escopo mesmo sem NBS: NbsAplicavelService.validarNbsAplicavel roda com NBS nulo quando o item não
        tem NCM nem NBS e não há tpDoc, e NomenclaturaService.determinarNomenclatura o consulta sempre.
        """
        return self._existe(
            "SELECT EXISTS (SELECT 1 FROM NBS_APLICAVEL n WHERE n.NBSA_CLTR_ID = :id "
            "AND :data BETWEEN n.NBSA_INICIO_VIGENCIA AND COALESCE(n.NBSA_FIM_VIGENCIA, :data))",
            id=id_classificacao, data=_iso(data))

    # --- alíquotas ----------------------------------------------------------------------

    def buscar_aliquota_referencia(self, id_tributo: int, data: date) -> Decimal | None:
        """AliquotaReferenciaRepository.buscar (via AliquotaReferenciaService.buscar). SQL gerado da JPQL."""
        linha = self._unico(
            f"Alíquota de referência do tributo {id_tributo} em {_iso(data)}",
            "SELECT CAST(ar.ALRE_VALOR AS TEXT) FROM ALIQUOTA_REFERENCIA ar WHERE ar.ALRE_TBTO_ID = :id_tributo "
            "AND :data BETWEEN ar.ALRE_INICIO_VIGENCIA AND COALESCE(ar.ALRE_FIM_VIGENCIA, :data)",
            id_tributo=id_tributo, data=_iso(data))
        return None if linha is None else _converter(linha[0], "n")

    def buscar_aliquota(self, id_tributo: int, codigo_uf, municipio, data: date) -> dict | None:
        """AliquotaPadraoRepository.buscarAliquota (via AliquotaPadraoService.buscarAliquota).

        SQL gerado da JPQL: `p.municipio.codigo` e `p.uf.codigo` são as chaves estrangeiras. Devolve os
        campos crus do AliquotaResultadoDTO {valorReferencia, valorPadrao, formaAplicacao}; o construtor do
        record (que valida e soma ou subtrai a padrão da referência e levanta as exceções de alíquota) é do
        motor, assim como a AliquotaNaoEncontradaException quando não há linha.
        """
        linha = self._unico(
            f"Alíquota do tributo {id_tributo}, UF {codigo_uf}, município {municipio}, em {_iso(data)}",
            "SELECT CAST(r.ALRE_VALOR AS TEXT), CAST(p.ALPA_VALOR AS TEXT), p.ALPA_FORMA_APLICACAO "
            "FROM ALIQUOTA_REFERENCIA r "
            "LEFT JOIN ALIQUOTA_PADRAO p ON p.ALPA_ALRE_ID = r.ALRE_ID "
            "AND ((:municipio IS NULL AND p.ALPA_MUNI_CD IS NULL) "
            "OR (:municipio IS NOT NULL AND p.ALPA_MUNI_CD = :municipio)) "
            "AND ((:codigo_uf IS NULL AND p.ALPA_UF_CD IS NULL) "
            "OR (:codigo_uf IS NOT NULL AND p.ALPA_UF_CD = :codigo_uf)) "
            "AND p.ALPA_INICIO_VIGENCIA <= :data "
            "AND (p.ALPA_FIM_VIGENCIA IS NULL OR p.ALPA_FIM_VIGENCIA >= :data) "
            "WHERE r.ALRE_TBTO_ID = :id_tributo AND r.ALRE_INICIO_VIGENCIA <= :data "
            "AND (r.ALRE_FIM_VIGENCIA IS NULL OR r.ALRE_FIM_VIGENCIA >= :data)",
            id_tributo=id_tributo, codigo_uf=codigo_uf, municipio=municipio, data=_iso(data))
        if linha is None:
            return None
        return {"valorReferencia": _converter(linha[0], "n"), "valorPadrao": _converter(linha[1], "n"),
                "formaAplicacao": linha[2]}

    @staticmethod
    def _sql_aliquota_produto(selecao, produto, p, taxa, t, excecao, e, cauda):
        """O corpo comum das consultas de alíquota de produto (ad valorem e ad rem): o produto cujo NCM é
        prefixo do informado, com o tributo, vigente, sem exceção vigente que cubra o NCM informado nem o
        prefixo do produto, e só se o NCM informado existe na tabela NCM (sem olhar vigência)."""
        excecoes = "".join(
            f"AND NOT EXISTS (SELECT 1 FROM {excecao} x WHERE x.{e}_NCM_CD = SUBSTR({alvo}, 1, LENGTH(x.{e}_NCM_CD)) "
            f"AND x.{e}_{p}_ID = a.{p}_ID "
            f"AND :data BETWEEN x.{e}_INICIO_VIGENCIA AND COALESCE(x.{e}_FIM_VIGENCIA, :data)) "
            for alvo in (":ncm", f"a.{p}_NCM_CD"))
        return (f"SELECT {selecao} FROM {produto} a JOIN {taxa} v ON v.{t}_ID = a.{p}_{t}_ID {{juntar}}"
                "WHERE EXISTS (SELECT 1 FROM NCM n WHERE n.NCM_CD = :ncm) "
                f"AND a.{p}_NCM_CD = SUBSTR(:ncm, 1, LENGTH(a.{p}_NCM_CD)) "
                f"AND v.{t}_TBTO_ID = :id_tributo "
                f"AND :data BETWEEN a.{p}_INICIO_VIGENCIA AND COALESCE(a.{p}_FIM_VIGENCIA, :data) "
                f"{excecoes}{cauda}")

    def buscar_aliquota_ad_valorem(self, ncm: str, id_tributo: int, data: date) -> dict | None:
        """AliquotaAdValoremProdutoRepository.buscarAliquotaAdValorem (via AliquotaAdValoremProdutoService).

        SQL gerado da JPQL: `a.aliquotaAdValorem.*` vira INNER JOIN com ALIQUOTA_AD_VALOREM, `a.ncm.codigo`
        e `e.ncm.codigo` são as chaves estrangeiras. ORDER BY LENGTH do prefixo DESC LIMIT 1: o prefixo mais
        longo vence. Devolve o AliquotaAdValoremDTO {valor} (o valor pode ser nulo com a linha existindo).
        """
        sql = self._sql_aliquota_produto(
            "CAST(v.AADV_VALOR AS TEXT)", "ALIQUOTA_AD_VALOREM_PRODUTO", "AAVP", "ALIQUOTA_AD_VALOREM", "AADV",
            "EXCECAO_AD_VALOREM_PRODUTO", "EAVP", "ORDER BY LENGTH(a.AAVP_NCM_CD) DESC LIMIT 1").format(juntar="")
        linhas = self._consultar(sql, ncm=ncm, id_tributo=id_tributo, data=_iso(data))[1]
        return {"valor": _converter(linhas[0][0], "n")} if linhas else None

    def existe_ncm_ad_valorem(self, ncm: str, id_tributo: int, data: date) -> bool:
        """AliquotaAdValoremProdutoRepository.existeNcmAdValorem (o TributoEnum entra pelo código).

        SQL gerado da JPQL `SELECT count(1) > 0`, com o mesmo filtro de buscarAliquotaAdValorem.
        """
        sql = self._sql_aliquota_produto(
            "COUNT(1) > 0", "ALIQUOTA_AD_VALOREM_PRODUTO", "AAVP", "ALIQUOTA_AD_VALOREM", "AADV",
            "EXCECAO_AD_VALOREM_PRODUTO", "EAVP", "").format(juntar="")
        return self._existe(sql, ncm=ncm, id_tributo=id_tributo, data=_iso(data))

    def buscar_aliquota_ad_rem(self, ncm: str, id_tributo: int, data: date) -> dict | None:
        """AliquotaAdRemProdutoRepository.buscarAliquotaAdRem (via AliquotaAdRemProdutoService).

        SQL gerado da JPQL: `a.aliquotaAdRem.unidadeMedida.sigla` vira INNER JOIN com ALIQUOTA_AD_REM e com
        UNIDADE_MEDIDA. Devolve o AliquotaAdRemDTO {valor, unidadeMedida}: o DTO vem mesmo com valor nulo, e
        é a existência dele que ProcessamentoItemService.existeIncidenciaImpostoSeletivo testa.
        """
        sql = self._sql_aliquota_produto(
            "CAST(v.AARE_VALOR AS TEXT), u.UNMD_SIGLA", "ALIQUOTA_AD_REM_PRODUTO", "AARP", "ALIQUOTA_AD_REM", "AARE",
            "EXCECAO_AD_REM_PRODUTO", "EARP", "ORDER BY LENGTH(a.AARP_NCM_CD) DESC LIMIT 1").format(
            juntar="JOIN UNIDADE_MEDIDA u ON u.UNMD_ID = v.AARE_UNMD_ID ")
        linhas = self._consultar(sql, ncm=ncm, id_tributo=id_tributo, data=_iso(data))[1]
        if not linhas:
            return None
        return {"valor": _converter(linhas[0][0], "n"), "unidadeMedida": linhas[0][1]}

    def buscar_aliquota_ad_valorem_dto(self, nbs, id_tributo: int, id_classificacao, data: date) -> dict | None:
        """AliquotaAdValoremServicoRepository.buscarAliquotaAdValoremDto (CalculoCbsIbsService, tipo de
        alíquota "Uniforme setorial", com o NBS do item).

        No escopo da skill o NBS é sempre nulo, e o EXISTS em NBS com código nulo zera a consulta; o port fica
        inteiro para o motor chamar como o Java chama. SQL gerado da JPQL (sem LIMIT: mais de uma linha é
        ResultadoNaoUnico, que o service troca por ErroGenericoValidacaoException).
        """
        linha = self._unico(
            f"Alíquota ad valorem do NBS {nbs}, tributo {id_tributo}, classificação {id_classificacao}",
            "SELECT CAST(v.AADV_VALOR AS TEXT) FROM ALIQUOTA_AD_VALOREM_SERVICO a "
            "JOIN ALIQUOTA_AD_VALOREM v ON v.AADV_ID = a.AAVS_AADV_ID "
            "WHERE EXISTS (SELECT 1 FROM NBS n WHERE n.NBS_CD = :nbs) "
            "AND a.AAVS_NBS_CD = SUBSTR(:nbs, 1, LENGTH(a.AAVS_NBS_CD)) "
            "AND v.AADV_TBTO_ID = :id_tributo "
            "AND (:id_classificacao IS NULL OR v.AADV_CLTR_ID = :id_classificacao) "
            "AND :data BETWEEN a.AAVS_INICIO_VIGENCIA AND COALESCE(a.AAVS_FIM_VIGENCIA, :data) "
            "AND NOT EXISTS (SELECT 1 FROM EXCECAO_AD_VALOREM_SERVICO x "
            "WHERE x.EAVS_NBS_CD = SUBSTR(:nbs, 1, LENGTH(x.EAVS_NBS_CD)) AND x.EAVS_AAVS_ID = a.AAVS_ID "
            "AND :data BETWEEN x.EAVS_INICIO_VIGENCIA AND COALESCE(x.EAVS_FIM_VIGENCIA, :data)) "
            "AND NOT EXISTS (SELECT 1 FROM EXCECAO_AD_VALOREM_SERVICO x "
            "WHERE x.EAVS_NBS_CD = SUBSTR(a.AAVS_NBS_CD, 1, LENGTH(x.EAVS_NBS_CD)) AND x.EAVS_AAVS_ID = a.AAVS_ID "
            "AND :data BETWEEN x.EAVS_INICIO_VIGENCIA AND COALESCE(x.EAVS_FIM_VIGENCIA, :data)) "
            "ORDER BY LENGTH(a.AAVS_NBS_CD) DESC",
            nbs=nbs, id_tributo=id_tributo, id_classificacao=id_classificacao, data=_iso(data))
        return None if linha is None else {"valor": _converter(linha[0], "n")}

    def buscar_aliquota_ad_valorem_por_classificacao_tributaria(self, id_tributo: int, id_classificacao: int,
                                                                data: date) -> Decimal | None:
        """AliquotaAdValoremServicoRepository.buscarAliquotaAdValoremPorClassificacaoTributaria (native query).

        A única leitura de BigDecimal sem CAST AS TEXT: na native query o valor chega ao Java como número de
        ponto flutuante, e vira BigDecimal pelo texto mais curto dele (tipo "f"; veja a docstring do módulo).
        CalculoCbsIbsService: tipo de alíquota "Fixa" e o segundo passo de "Uniforme setorial". Mais de uma
        linha é ResultadoNaoUnico (o service troca por ErroGenericoValidacaoException).
        """
        linha = self._unico(
            f"Alíquota ad valorem do tributo {id_tributo} e classificação {id_classificacao} em {_iso(data)}",
            "SELECT aadv.AADV_VALOR FROM ALIQUOTA_AD_VALOREM aadv WHERE aadv.AADV_TBTO_ID = :id_tributo "
            "AND aadv.AADV_CLTR_ID = :id_classificacao "
            "AND :data BETWEEN aadv.AADV_INICIO_VIGENCIA AND COALESCE(aadv.AADV_FIM_VIGENCIA, :data)",
            id_tributo=id_tributo, id_classificacao=id_classificacao, data=_iso(data))
        return None if linha is None else _converter(linha[0], "f")

    def get_percentual_transferencia(self, data: date) -> Decimal | None:
        """TransferenciaCBSRepository.getPercentualTransferencia (Optional<BigDecimal>). SQL gerado da JPQL.

        Chamado por CalculoTributoService sempre que há CBS ou IBS calculado, com ou sem gCompraGov.
        """
        linha = self._unico(
            f"Percentual de transferência da CBS em {_iso(data)}",
            "SELECT CAST(TCEG_VALOR AS TEXT) FROM TRANSFERENCIA_CBS_ENTE_GOV WHERE TCEG_INICIO_VIGENCIA <= :data "
            "AND (TCEG_FIM_VIGENCIA IS NULL OR TCEG_FIM_VIGENCIA >= :data)",
            data=_iso(data))
        return None if linha is None else _converter(linha[0], "n")

    # --- atalhos com o nome do plano (mesmo comportamento dos services Java) -------------------

    def percentual_transferencia(self, data: date) -> Decimal:
        """TransferenciaCBSService.getPercentualTransferencia: sem linha, RuntimeException (HTTP 500)."""
        valor = self.get_percentual_transferencia(data)
        if valor is None:
            raise RuntimeError("Percentual de transferência de CBS para ente federativo não encontrada.")
        return valor

    def aliquota_ad_valorem_produto(self, ncm: str, data: date) -> Decimal | None:
        """O valor da alíquota ad valorem do Imposto Seletivo (tributo 1) do NCM, como
        ProcessamentoItemService.analisarAliquotaImpostoSeletivo a usa: DTO ausente ou valor nulo dão None."""
        dto = self.buscar_aliquota_ad_valorem(ncm, TRIBUTO_IS, data)
        return None if dto is None else dto["valor"]

    # --- nomenclatura (NomenclaturaService) ---------------------------------------------

    def determinar_nomenclatura(self, sigla_dfe: str, c_class_trib: str, data: date) -> str:
        """NomenclaturaService.determinarNomenclatura: SEM, NCM, NBS, MISTO, EXCECAO_NCM ou EXCECAO_NBS.

        É aqui que EXCEPTION_CLASSIFICACOES vale: 200038 e 515001 dão EXCECAO_NCM na NF-e e na NFC-e,
        EXCECAO_NBS na NFS-e e SEM nos demais documentos, desde que a classificação tenha vínculo com o
        documento (sem vínculo é SEM, antes de olhar a lista).
        """
        classificacao = self.buscar_classificacao_tributaria(c_class_trib, TRIBUTO_CBS, data)
        if classificacao is None:  # ClassificacaoTributariaService.buscarClassificacaoTributariaCbsIbs
            raise ClassificacaoTributariaNaoEncontradaException(c_class_trib, "CBS e IBS", _iso(data))
        id_classificacao = classificacao["id"]
        tipos = self.buscar_tipo_dfe_classificacao(id_classificacao, data)
        normalizada = sigla_dfe_por_normalizada(sigla_dfe)  # valida a sigla informada
        if not any(normalizar_sigla(t["tipoDfe"]["sigla"]) == normalizada for t in tipos):
            return "SEM"
        com_ncm = self.tem_ncm_aplicavel_para_classificacao(id_classificacao, data)
        com_nbs = self.tem_nbs_aplicavel_para_classificacao(id_classificacao, data)
        return self._determinar_resultado(sigla_dfe, c_class_trib, com_ncm, com_nbs)

    @staticmethod
    def _determinar_resultado(sigla_dfe, c_class_trib, com_ncm, com_nbs) -> str:
        # NomenclaturaService.determinarResultado e tratarCasoExcecao
        if c_class_trib in EXCEPTION_CLASSIFICACOES:
            normalizada = sigla_dfe_por_normalizada(sigla_dfe)
            if normalizada in ("NFE", "NFCE"):
                return "EXCECAO_NCM"
            if normalizada == "NFSE":
                return "EXCECAO_NBS"
            return "SEM"
        if ncm_aplicavel(sigla_dfe) and not nbs_aplicavel(sigla_dfe):
            return "NCM" if com_ncm else "SEM"
        if nbs_aplicavel(sigla_dfe):
            if com_ncm and com_nbs:
                return "MISTO"
            if com_ncm:
                return "NCM"
            if com_nbs:
                return "NBS"
            return "SEM"
        return "SEM"
