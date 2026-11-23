"""Leitura da base da Receita Federal para a skill cbs-ibs-simples.

Port das consultas da calculadora oficial do Simples Nacional (tag 1.0.2): repositórios,
ParametroCalculoSimplesService, validação de nomenclatura (domain/service) e os endpoints de
atividades. A base é o SQLite `base-receita.db` montado pela Tarefa 2.

Convenções:
- Cada método que espelha um método Java diz, em comentário curto, a classe e o método de origem.
- Linhas saem como `dict` com os nomes dos campos das entidades Java. Datas são `datetime.date`,
  booleanos são `bool`, BigDecimal é `decimal.Decimal`, enums são o nome do enum (texto).
- A vigência é a do Java: `data BETWEEN inicio AND COALESCE(fim, data)`.
- Sem cache além de um dicionário por instância. A base abre somente leitura.
- Só biblioteca padrão. Esta lib não arredonda nada: arredondamento mora em numeros.py.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote

NOME_BASE = "base-receita.db"

# Valores aceitos como "anexo" (DerivacaoAnexosSimples.ANEXOS_VALIDOS), em ordem de romano.
ANEXOS_VALIDOS = ("I", "II", "III", "IV", "V")

MODO_PERMISSIVO = "PERMISSIVO"  # ModoValidacaoNomenclatura
MODO_RIGIDO = "RIGIDO"

# Endpoint de atividades (AtividadeSimplesController) com o context-path /api do perfil offline.
CAMINHO_ATIVIDADES = "/api/calculadora/simples-nacional/atividades"
BASE_URL_PADRAO = "http://127.0.0.1"  # HttpUtils.getBaseURL: esquema + host, sem porta

# SiglasDFeEnum: nome do enum -> sigla.
SIGLAS_DFE = {
    "NFEABI": "NFe ABI", "NFE": "NFe", "NFCE": "NFCe", "CTE": "CTe", "CTEOS": "CTe OS",
    "CTESIMPLIFICADO": "CT-e Simplificado", "BPE": "BPe", "BPETA": "BPe TA", "BPETM": "BPe TM",
    "NF3E": "NF3e", "NFSE": "NFSe", "NFSEVIA": "NFSe Via", "NFCOM": "NFCom", "DERE": "DERE",
    "DIR": "DIR", "DUIMP": "Duimp", "NFAG": "NFAg", "NFGAS": "NFGas",
}
_NCM_APLICAVEL = frozenset({"NFSE", "NFE", "NFCE"})
_NBS_APLICAVEL = frozenset({"NFSE"})
_TRIO_RIGIDO = frozenset({"NFE", "NFCE", "NFSE"})
_EXCEPTION_CLASSIFICACOES = frozenset({"200038"})  # NomenclaturaService.EXCEPTION_CLASSIFICACOES
_SEGURO_NO_CAMINHO = "-._~!$&'()*+,;=:@"  # caracteres que o requestURI deixa passar sem escape
_ESPACOS_JAVA = "".join(chr(i) for i in range(0x21))  # String.trim tira tudo até U+0020


# --- Erros --------------------------------------------------------------------------------

class BaseIndisponivel(Exception):
    """A base não foi achada, não abriu ou não é a base da Receita."""


class ValidacaoException(Exception):
    """ValidacaoException.java (base das críticas de nomenclatura)."""


class EntidadeNaoEncontradaException(Exception):
    """EntidadeNaoEncontradaException.java."""


class ResultadoNaoUnico(Exception):
    """IncorrectResultSizeDataAccessException: a consulta devia trazer uma linha só."""


class NcmCompletoNaoInformadoException(ValidacaoException):
    def __init__(self):
        super().__init__("NCM completo não informado")


class NbsCompletoNaoInformadoException(ValidacaoException):
    def __init__(self):
        super().__init__("NBS completo não informado")


class NcmNaoVinculadaException(ValidacaoException):
    def __init__(self, ncm, c_class_trib, tributo):
        super().__init__(f"NCM de código {ncm} não vinculada à Classificação Tributária de código "
                         f"{c_class_trib} ({tributo})")


class NbsNaoVinculadaException(ValidacaoException):
    def __init__(self, nbs, c_class_trib, tributo):
        super().__init__(f"NBS de código {nbs} não vinculada à Classificação Tributária de código "
                         f"{c_class_trib} ({tributo})")


class NcmNbsSimultaneasException(ValidacaoException):
    def __init__(self):
        super().__init__("Não é permitido informar NCM e NBS simultaneamente para um item")


class NomenclaturaIncompativelTipoDfeException(ValidacaoException):
    def __init__(self, nomenclatura, c_class_trib, sigla_dfe):
        super().__init__(f"{nomenclatura} não permitida para a Classificação Tributária de código "
                         f"{c_class_trib} no tipo de documento {sigla_dfe}")


class NomenclaturaNaoPermitidaTipoDfeException(ValidacaoException):
    def __init__(self, c_class_trib, sigla_dfe):
        super().__init__(f"NCM/NBS não permitido para a Classificação Tributária de código "
                         f"{c_class_trib} no tipo de documento {sigla_dfe}")


class NomenclaturaObrigatoriaNaoInformadaException(ValidacaoException):
    def __init__(self, c_class_trib, sigla_dfe):
        super().__init__(f"A Classificação Tributária de código {c_class_trib} exige NCM ou NBS "
                         f"completo para o tipo de documento {sigla_dfe}")


class SiglaDFeNaoEncontradaException(ValidacaoException):
    def __init__(self, sigla):
        super().__init__(f"Sigla DFe inválida: {sigla}")


class NcmNaoEncontradaException(EntidadeNaoEncontradaException):
    def __init__(self, ncm, data):
        super().__init__(f"NCM de código {ncm} não encontrada para a data {data}")


class NbsNaoEncontradaException(EntidadeNaoEncontradaException):
    def __init__(self, nbs, data):
        super().__init__(f"NBS de código {nbs} não encontrada para a data {data}")


class ClassificacaoTributariaNaoEncontradaException(EntidadeNaoEncontradaException):
    def __init__(self, codigo, tributo, data):
        super().__init__(f"Classificação tributária não encontrada para código {codigo} e data "
                         f"{data} ({tributo})")


class _ErroHttp(Exception):
    """ResponseStatusException: status e motivo, que viram o corpo do ApiExceptionHandler."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


class _ErroSistema(Exception):
    """Exception que o ApiExceptionHandler não trata: HTTP 500."""

    def __init__(self, detalhe: str):
        super().__init__(detalhe)
        self.detalhe = detalhe


class _ErroTipoArgumento(Exception):
    """MethodArgumentTypeMismatchException (parâmetro com valor mal formado)."""

    def __init__(self, nome: str, valor):
        super().__init__(nome)
        self.nome = nome
        self.valor = valor


class _ErroParametroAusente(Exception):
    """MissingServletRequestParameterException (parâmetro obrigatório ausente)."""

    def __init__(self, nome: str, tipo: str):
        super().__init__(nome)
        self.nome = nome
        self.tipo = tipo


# --- Mapeamento coluna -> campo -----------------------------------------------------------
# Tipos: s texto, i inteiro, b booleano, d data, n Decimal.

_ATIVIDADE = (
    ("id", "ATSN_ID", "i"), ("ordem", "ATSN_ORDEM", "i"), ("descricao", "ATSN_DESCRICAO", "s"),
    ("anexoSemFatorR", "ATSN_ANEXO_SEM_FATOR_R", "s"), ("anexoComFatorR", "ATSN_ANEXO_COM_FATOR_R", "s"),
    ("rotulo", "ATSN_ROTULO", "s"), ("rotuloCurto", "ATSN_ROTULO_CURTO", "s"),
    ("inicioVigencia", "ATSN_INICIO_VIGENCIA", "d"), ("fimVigencia", "ATSN_FIM_VIGENCIA", "d"),
    ("cAtivSN", "ATSN_CATIVSN", "s"), ("naoTributavel", "ATSN_IN_NAO_TRIBUTAVEL", "b"),
    ("issNoSimples", "ATSN_IN_ISS_NO_SIMPLES", "b"),
    ("admiteBeneficioMunicipal", "ATSN_IN_ADMITE_BENEFICIO_MUNICIPAL", "b"),
)
_TIPO_DFE_CALCULO = (
    ("id", "TDCS_ID", "i"), ("tipoDfeId", "TDCS_TPDF_ID", "i"),
    ("vocabularioRegime", "TDCS_VOCABULARIO_REGIME", "s"),
    ("casasAliquotaCalculo", "TDCS_CASAS_ALIQUOTA_CALCULO", "i"),
    ("maximoItens", "TDCS_MAXIMO_ITENS", "i"), ("admiteImpostoSeletivo", "TDCS_IN_ADMITE_IS", "b"),
    ("inicioVigencia", "TDCS_INICIO_VIGENCIA", "d"), ("fimVigencia", "TDCS_FIM_VIGENCIA", "d"),
)
_TIPO_RECEITA = (
    ("id", "TRBS_ID", "i"), ("codigo", "TRBS_CD", "i"), ("descricao", "TRBS_DESCRICAO", "s"),
    ("compoeReceita", "TRBS_IN_COMPOE_RECEITA", "b"),
    ("impostoSeletivoCompoeReceita", "TRBS_IN_IS_COMPOE_RECEITA", "b"),
    ("fatorReceita", "TRBS_FATOR_RECEITA", "n"), ("pendenteSuspenso", "TRBS_IN_PENDENTE_SUSPENSO", "b"),
    ("exigeAliquota", "TRBS_IN_EXIGE_ALIQUOTA", "b"),
    ("vedadoNaoTributavel", "TRBS_IN_VEDADO_NAO_TRIBUTAVEL", "b"),
    ("vocabularioRegime", "TRBS_VOCABULARIO_REGIME", "s"),
    ("inicioVigencia", "TRBS_INICIO_VIGENCIA", "d"), ("fimVigencia", "TRBS_FIM_VIGENCIA", "d"),
)
_REGIME = (
    ("id", "RASN_ID", "i"), ("codigo", "RASN_CD", "s"), ("descricao", "RASN_DESCRICAO", "s"),
    ("calculaTributos", "RASN_IN_CALCULA_TRIBUTOS", "b"), ("geraIbs", "RASN_IN_GERA_IBS", "b"),
    ("destinoDevido", "RASN_DESTINO_DEVIDO", "s"),
    ("destinoPendenteSuspenso", "RASN_DESTINO_PENDENTE_SUSPENSO", "s"),
    ("inicioVigencia", "RASN_INICIO_VIGENCIA", "d"), ("fimVigencia", "RASN_FIM_VIGENCIA", "d"),
)
_ENQUADRAMENTO = (
    ("id", "ERSN_ID", "i"), ("vocabularioRegime", "ERSN_VOCABULARIO_REGIME", "s"),
    ("crt", "ERSN_CRT", "i"), ("tpRegimeIBSCBS", "ERSN_TPREGIMEIBSCBS", "i"),
    ("opSimpNac", "ERSN_OPSIMPNAC", "i"), ("regApIBSCBSSN", "ERSN_REGAPIBSCBSSN", "i"),
    ("inicioVigencia", "ERSN_INICIO_VIGENCIA", "d"), ("fimVigencia", "ERSN_FIM_VIGENCIA", "d"),
)
_SITUACAO_ISS = (
    ("id", "SIPA_ID", "i"), ("campo", "SIPA_CAMPO", "s"), ("valor", "SIPA_VALOR", "i"),
    ("regra", "SIPA_REGRA", "s"), ("tipoAliquota", "SIPA_TIPO_ALIQUOTA", "s"),
    ("ordem", "SIPA_ORDEM", "i"), ("inicioVigencia", "SIPA_INICIO_VIGENCIA", "d"),
    ("fimVigencia", "SIPA_FIM_VIGENCIA", "d"),
)
_TRANSICAO = (
    ("id", "TACG_ID", "i"), ("fator", "TACG_FATOR", "n"),
    ("inicioVigencia", "TACG_INICIO_VIGENCIA", "d"), ("fimVigencia", "TACG_FIM_VIGENCIA", "d"),
)
_CLASSIFICACAO = (
    ("id", "CLTR_ID", "i"), ("codigo", "CLTR_CD", "s"), ("tpRBSN", "CLTR_TPRBSN", "i"),
    ("inicioVigencia", "CLTR_INICIO_VIGENCIA", "d"), ("fimVigencia", "CLTR_FIM_VIGENCIA", "d"),
)
_TIPO_DFE = (
    ("id", "TPDF_ID", "i"), ("tipo", "TPDF_TIPO", "i"), ("sigla", "TPDF_SIGLA", "s"),
    ("descricao", "TPDF_DESCRICAO", "s"), ("inicioVigencia", "TPDF_INICIO_VIGENCIA", "d"),
    ("fimVigencia", "TPDF_FIM_VIGENCIA", "d"),
)


def _converter(valor, tipo):
    if valor is None:
        return None
    if tipo == "b":
        return bool(valor)
    if tipo == "d":
        return date.fromisoformat(str(valor)[:10])
    if tipo == "n":
        return Decimal(repr(valor)) if isinstance(valor, float) else Decimal(str(valor))
    return valor


def _colunas(mapa, alias):
    return ", ".join(f"{alias}.{coluna}" for _, coluna, _ in mapa)


def _linha(mapa, valores):
    """Monta o dict de uma entidade a partir de uma fatia de valores, na ordem do mapa."""
    return {campo: _converter(valor, tipo) for (campo, _, tipo), valor in zip(mapa, valores)}


# --- Parâmetros de entrada ---------------------------------------------------------------

def _iso(data) -> str:
    if isinstance(data, _DataDeConsulta):
        return data.isoformat()
    if isinstance(data, datetime):
        data = data.date()
    if not isinstance(data, date):
        raise TypeError(f"data deve ser datetime.date, veio {type(data).__name__}")
    return data.isoformat()


_RE_DATA_ISO = re.compile(
    r"([+-]?)([0-9]{4,10})-([0-9]{2})-([0-9]{2})(?:[Zz]|[+-][0-9]{2}:[0-9]{2}(?::[0-9]{2})?)?\Z")


def ano_da_era(ano):
    """O `yyyy` do Java, com que a oficial grava a data na consulta: o ano 0 e os negativos viram 1 - ano.

    Causa: o perfil offline abre o SQLite com `date_string_format=yyyy-MM-dd` (application-offline.yml), e
    `yyyy` é o ano da era, não o ano proléptico."""
    return ano if ano >= 1 else 1 - ano


class _DataDeConsulta:
    """Data de consulta que o calendário do Python não representa (29 de fevereiro de um ano da era que não é bissexto)."""

    def __init__(self, texto: str):
        self._texto = texto

    def isoformat(self) -> str:
        return self._texto


def data_de_consulta(era, mes, dia):
    """A data, no ano da era (1 a 9999), como as consultas à base a recebem."""
    try:
        return date(era, mes, dia)
    except ValueError:
        return _DataDeConsulta(f"{era:04d}-{mes:02d}-{dia:02d}")


def _dias_do_mes(ano, mes):
    if mes == 2:
        return 29 if ano % 4 == 0 and (ano % 100 != 0 or ano % 400 == 0) else 28
    return 30 if mes in (4, 6, 9, 11) else 31


def _parse_data(valor):
    """@RequestParam LocalDate com @DateTimeFormat(iso = ISO.DATE): aceita o que a oficial aceita.

    Verificado contra a oficial: `2027-01-01+01:00` e `2027-01-01z` valem, assim como o ano 0000, o ano
    negativo (`-2027-01-01`) e o bissexto proléptico; `2027-1-1`, `2027-02-30`, `01/01/2027`, `-0000-01-01`,
    `+9999-01-01` e dígitos fora do ASCII são MethodArgumentTypeMismatchException; ausente ou vazio é
    MissingServletRequestParameter. Ano acima de 9999 é falha da oficial (HTTP 500).
    """
    if valor is None or valor == "":
        raise _ErroParametroAusente("data", "LocalDate")
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    if not isinstance(valor, str):
        raise TypeError(f"data deve ser date ou texto, veio {type(valor).__name__}")
    achou = _RE_DATA_ISO.match(valor)
    if not achou:
        raise _ErroTipoArgumento("data", valor)
    sinal, digitos, mes, dia = achou.groups()
    # SignStyle.EXCEEDS_PAD: o + só com mais de quatro dígitos, o - com qualquer número deles, nunca com ano zero
    if (len(digitos) > 4 and sinal == "") or (len(digitos) == 4 and sinal == "+"):
        raise _ErroTipoArgumento("data", valor)
    ano = int(digitos) * (-1 if sinal == "-" else 1)
    if (sinal == "-" and ano == 0) or abs(ano) > 999_999_999:
        raise _ErroTipoArgumento("data", valor)
    mes, dia = int(mes), int(dia)
    if not 1 <= mes <= 12 or not 1 <= dia <= _dias_do_mes(ano, mes):
        raise _ErroTipoArgumento("data", valor)
    era = ano_da_era(ano)
    if era > 9999:
        raise _ErroSistema("Index -1 out of bounds for length 4")
    return data_de_consulta(era, mes, dia)


def _converter_anexos(anexos):
    """AtividadeSimplesController.converterAnexos."""
    if anexos is None:
        return None
    if isinstance(anexos, str):
        anexos = [anexos]
    if len(anexos) == 1:
        # um só parâmetro: o Spring o parte nas vírgulas e apara cada pedaço (StringToCollectionConverter);
        # parâmetro repetido chega como veio
        anexos = [pedaco.strip(_ESPACOS_JAVA) for pedaco in anexos[0].split(",")]
    configurados = set()
    for elemento in anexos:
        for anexo in elemento.split(","):
            if anexo == "":
                continue
            if anexo not in ANEXOS_VALIDOS:
                raise _ErroHttp(400, f"Anexo inválido: '{anexo}'. Valores aceitos: "
                                     f"{', '.join(ANEXOS_VALIDOS)}.")
            configurados.add(anexo)
    return configurados


def derivar_anexos(anexo_sem_fator_r, anexo_com_fator_r, anexos_configurados):
    """DerivacaoAnexosSimples.derivar. Devolve {disponivel, anexosFaltantes, modoFatorR}."""
    if anexo_com_fator_r is not None and anexo_sem_fator_r is None:
        raise RuntimeError(
            f"Atividade inconsistente: anexoComFatorR ({anexo_com_fator_r}) preenchido sem "
            "anexoSemFatorR. Não existe atividade \"só com Fator R\"; corrija o banco.")
    configurados = set(ANEXOS_VALIDOS) if anexos_configurados is None else set(anexos_configurados)
    possiveis = [a for a in ANEXOS_VALIDOS if a in (anexo_sem_fator_r, anexo_com_fator_r)]
    faltantes = [a for a in possiveis if a not in configurados]
    disponivel = not possiveis or any(a in configurados for a in possiveis)
    if anexo_com_fator_r is None:
        modo = "NAO_APLICAVEL"
    elif anexo_com_fator_r in configurados:
        modo = "OPCIONAL" if anexo_sem_fator_r in configurados else "OBRIGATORIO"
    else:
        modo = "INDISPONIVEL"
    return {"disponivel": disponivel, "anexosFaltantes": faltantes, "modoFatorR": modo}


# --- Siglas de DF-e -----------------------------------------------------------------------

def normalizar_sigla(sigla) -> str:
    """SiglasDFeEnum.normalizarSigla."""
    if sigla is None:
        return ""
    return re.sub(r"[ \t\n\x0b\f\r\-_]", "", sigla.upper())


def sigla_dfe_por_normalizada(sigla) -> str:
    """SiglasDFeEnum.getPorSiglaNormalizada: devolve o nome do enum ou levanta a exceção."""
    normalizada = normalizar_sigla(sigla)
    if normalizada in SIGLAS_DFE:
        return normalizada
    raise SiglaDFeNaoEncontradaException(sigla)


def _tamanho(texto) -> int:  # StringUtils.length
    return 0 if texto is None else len(texto)


def _preenchido(texto) -> bool:  # StringUtils.isNotBlank
    return texto is not None and texto.strip() != ""


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

    # --- metadados --------------------------------------------------------------------

    def meta(self) -> dict:
        """CD2_META, chave 'meta' (mesmo conteúdo de ler_meta em scripts/receita_base.py)."""
        return json.loads(json.dumps(self._meta))

    def versao_base(self) -> dict:
        """VersaoBaseDadosService.getUltimaVersao: {versao, descricao, data}."""
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

    # --- ParametroCalculoSimplesService e repositórios do Simples ---------------------------

    def tp_doc_suportado(self, tp_doc: int, data: date) -> bool:
        # ParametroCalculoSimplesService.tpDocSuportado
        return self.contar_vinculos_tipo_dfe_calculo(tp_doc, data) > 0

    def tp_doc_sem_tratamento(self, tp_doc: int, data: date) -> bool:
        # ParametroCalculoSimplesService.tpDocSemTratamentoCalculadora
        return self.contar_sem_tratamento_calculadora(tp_doc, data) > 0

    def tipo_dfe_calculo(self, tp_doc: int, data: date):
        # ParametroCalculoSimplesService.buscarTipoDfeCalculo (primeiro da lista, ou None)
        vigentes = self.tipos_dfe_calculo_vigentes(tp_doc, data)
        return vigentes[0] if vigentes else None

    def tipo_receita_bruta(self, tp_rbsn: int, data: date, vocabulario=None):
        # ParametroCalculoSimplesService.buscarTipoReceitaBruta, as duas sobrecargas:
        # sem vocabulário vale a linha padrão; com vocabulário, a dele ou a padrão.
        if vocabulario is None:
            vigentes = self.tipos_receita_bruta_por_codigo(tp_rbsn, data)
        else:
            vigentes = self.tipos_receita_bruta_por_codigo_e_vocabulario(tp_rbsn, vocabulario, data)
        return vigentes[0] if vigentes else None

    def regime_apuracao(self, vocabulario, crt, tp_regime_ibscbs, op_simp_nac, reg_ap_ibscbssn,
                        data: date):
        # ParametroCalculoSimplesService.buscarRegimeApuracao (sobrecarga com os campos do
        # documento): devolve a linha do regime do primeiro enquadramento vigente, ou None.
        vigentes = self.enquadramentos_vigentes(vocabulario, crt, tp_regime_ibscbs, op_simp_nac,
                                                reg_ap_ibscbssn, data)
        return vigentes[0]["regime"] if vigentes else None

    def regime_apuracao_por_regime(self, regime: str, data: date):
        # ParametroCalculoSimplesService.buscarRegimeApuracao (sobrecarga com
        # RegimeCalculoSimplesEnum): CRT_1, CRT_2 ou CRT_3_TPREGIME_2.
        if regime == "CRT_1":
            return self.regime_apuracao("CRT", 1, None, None, None, data)
        if regime == "CRT_2":
            return self.regime_apuracao("CRT", 2, None, None, None, data)
        if regime == "CRT_3_TPREGIME_2":
            return self.regime_apuracao("CRT", 3, 2, None, None, data)
        raise ValueError(f"RegimeCalculoSimplesEnum desconhecido: {regime}")

    def fator_transicao_compra_gov(self, data: date):
        # ParametroCalculoSimplesService.buscarFatorTransicaoCompraGov (Decimal ou None)
        vigentes = self.transicoes_compra_gov_vigentes(data)
        return vigentes[0]["fator"] if vigentes else None

    def situacoes_iss_par_aliquota(self, data: date) -> list:
        # ParametroCalculoSimplesService.buscarSituacoesIssParAliquota
        # = SituacaoIssParAliquotaSimplesRepository.buscarVigentes
        return self._linhas(
            _SITUACAO_ISS,
            f"SELECT {_colunas(_SITUACAO_ISS, 's')} FROM SITUACAO_ISS_PAR_ALIQUOTA_SIMPLES_NACIONAL s "
            "WHERE :data BETWEEN s.SIPA_INICIO_VIGENCIA AND COALESCE(s.SIPA_FIM_VIGENCIA, :data) "
            "ORDER BY s.SIPA_ORDEM ASC, s.SIPA_ID ASC", data=_iso(data))

    def classificacao(self, c_class_trib: str, data: date) -> list:
        # ClassificacaoTributariaSimplesRepository.buscarVigentes: lista, da vigência mais recente
        # para a mais antiga; o item aplicável é o primeiro. Só o grupo da CBS (tributo 2).
        return self._linhas(
            _CLASSIFICACAO,
            f"SELECT {_colunas(_CLASSIFICACAO, 'ct')} FROM CLASSIFICACAO_TRIBUTARIA ct "
            "WHERE ct.CLTR_CD = :codigo "
            "AND :data BETWEEN ct.CLTR_INICIO_VIGENCIA AND COALESCE(ct.CLTR_FIM_VIGENCIA, :data) "
            "AND ct.CLTR_SITR_ID IN (SELECT tst.TRST_SITR_ID FROM TRIBUTO_SITUACAO_TRIBUTARIA tst "
            "WHERE tst.TRST_TBTO_ID = 2) "
            "ORDER BY ct.CLTR_INICIO_VIGENCIA DESC", codigo=c_class_trib, data=_iso(data))

    def contar_vinculos_classificacao_tp_doc(self, c_class_trib: str, tp_doc: int, data: date) -> int:
        # ClassificacaoTributariaSimplesRepository.contarVinculosComTpDoc
        return self._contar(
            "SELECT COUNT(1) FROM TIPO_DFE_CLASSIFICACAO tdcl "
            "JOIN TIPO_DFE td ON td.TPDF_ID = tdcl.TDCL_TPDF_ID "
            "JOIN CLASSIFICACAO_TRIBUTARIA ct ON ct.CLTR_ID = tdcl.TDCL_CLTR_ID "
            "WHERE ct.CLTR_CD = :codigo AND td.TPDF_TIPO = :tpDoc "
            "AND ct.CLTR_SITR_ID IN (SELECT tst.TRST_SITR_ID FROM TRIBUTO_SITUACAO_TRIBUTARIA tst "
            "WHERE tst.TRST_TBTO_ID = 2) "
            "AND :data BETWEEN tdcl.TDCL_INICIO_VIGENCIA AND COALESCE(tdcl.TDCL_FIM_VIGENCIA, :data) "
            "AND :data BETWEEN td.TPDF_INICIO_VIGENCIA AND COALESCE(td.TPDF_FIM_VIGENCIA, :data)",
            codigo=c_class_trib, tpDoc=tp_doc, data=_iso(data))

    def contar_tratamentos_nao_implementados(self, c_class_trib: str, data: date) -> int:
        # ClassificacaoTributariaSimplesRepository.contarTratamentosNaoImplementados
        return self._contar(
            "SELECT COUNT(1) FROM TRATAMENTO_CLASSIFICACAO tc "
            "JOIN TRATAMENTO_TRIBUTARIO tt ON tt.TRTR_ID = tc.TRCL_TRTR_ID "
            "JOIN CLASSIFICACAO_TRIBUTARIA ct ON ct.CLTR_ID = tc.TRCL_CLTR_ID "
            "WHERE ct.CLTR_CD = :codigo AND tt.TRTR_IN_POSSUI_AJUSTE = 1 "
            "AND ct.CLTR_SITR_ID IN (SELECT tst.TRST_SITR_ID FROM TRIBUTO_SITUACAO_TRIBUTARIA tst "
            "WHERE tst.TRST_TBTO_ID = 2) "
            "AND :data BETWEEN ct.CLTR_INICIO_VIGENCIA AND COALESCE(ct.CLTR_FIM_VIGENCIA, :data) "
            "AND :data BETWEEN tc.TRCL_INICIO_VIGENCIA AND COALESCE(tc.TRCL_FIM_VIGENCIA, :data) "
            "AND :data BETWEEN tt.TRTR_INICIO_VIGENCIA AND COALESCE(tt.TRTR_FIM_VIGENCIA, :data)",
            codigo=c_class_trib, data=_iso(data))

    def enquadramentos_vigentes(self, vocabulario, crt, tp_regime_ibscbs, op_simp_nac,
                                reg_ap_ibscbssn, data: date) -> list:
        # EnquadramentoRegimeSimplesRepository.buscarEnquadramentosVigentes (JOIN FETCH do regime).
        # Campo None casa só com coluna nula. Cada item traz a linha do regime em "regime".
        sql = (
            f"SELECT {_colunas(_ENQUADRAMENTO, 'e')}, {_colunas(_REGIME, 'r')} "
            "FROM ENQUADRAMENTO_REGIME_SIMPLES_NACIONAL e "
            "JOIN REGIME_APURACAO_SIMPLES_NACIONAL r ON r.RASN_ID = e.ERSN_RASN_ID "
            "WHERE e.ERSN_VOCABULARIO_REGIME = :vocabulario "
            "AND ((:crt IS NULL AND e.ERSN_CRT IS NULL) OR e.ERSN_CRT = :crt) "
            "AND ((:tpRegimeIBSCBS IS NULL AND e.ERSN_TPREGIMEIBSCBS IS NULL) "
            "OR e.ERSN_TPREGIMEIBSCBS = :tpRegimeIBSCBS) "
            "AND ((:opSimpNac IS NULL AND e.ERSN_OPSIMPNAC IS NULL) OR e.ERSN_OPSIMPNAC = :opSimpNac) "
            "AND ((:regApIBSCBSSN IS NULL AND e.ERSN_REGAPIBSCBSSN IS NULL) "
            "OR e.ERSN_REGAPIBSCBSSN = :regApIBSCBSSN) "
            "AND :data BETWEEN e.ERSN_INICIO_VIGENCIA AND COALESCE(e.ERSN_FIM_VIGENCIA, :data) "
            "AND :data BETWEEN r.RASN_INICIO_VIGENCIA AND COALESCE(r.RASN_FIM_VIGENCIA, :data) "
            "ORDER BY e.ERSN_INICIO_VIGENCIA DESC, r.RASN_INICIO_VIGENCIA DESC")
        corte = len(_ENQUADRAMENTO)
        resultado = []
        for valores in self._consultar(
                sql, vocabulario=vocabulario, crt=crt, tpRegimeIBSCBS=tp_regime_ibscbs,
                opSimpNac=op_simp_nac, regApIBSCBSSN=reg_ap_ibscbssn, data=_iso(data))[1]:
            linha = _linha(_ENQUADRAMENTO, valores[:corte])
            linha["regime"] = _linha(_REGIME, valores[corte:])
            resultado.append(linha)
        return resultado

    def transicoes_compra_gov_vigentes(self, data: date) -> list:
        # TransicaoAliquotaCompraGovSimplesRepository.buscarVigentes
        return self._linhas(
            _TRANSICAO,
            f"SELECT {_colunas(_TRANSICAO, 't')} FROM TRANSICAO_ALIQUOTA_COMPRA_GOV_SIMPLES_NACIONAL t "
            "WHERE :data BETWEEN t.TACG_INICIO_VIGENCIA AND COALESCE(t.TACG_FIM_VIGENCIA, :data) "
            "ORDER BY t.TACG_INICIO_VIGENCIA DESC", data=_iso(data))

    def tipos_receita_bruta_por_codigo(self, tp_rbsn: int, data: date) -> list:
        # TipoReceitaBrutaSimplesRepository.buscarVigentesPorCodigo (só vocabulário nulo)
        return self._linhas(
            _TIPO_RECEITA,
            f"SELECT {_colunas(_TIPO_RECEITA, 't')} FROM TIPO_RECEITA_BRUTA_SIMPLES_NACIONAL t "
            "WHERE t.TRBS_CD = :codigo AND t.TRBS_VOCABULARIO_REGIME IS NULL "
            "AND :data BETWEEN t.TRBS_INICIO_VIGENCIA AND COALESCE(t.TRBS_FIM_VIGENCIA, :data) "
            "ORDER BY t.TRBS_INICIO_VIGENCIA DESC", codigo=tp_rbsn, data=_iso(data))

    def tipos_receita_bruta_por_codigo_e_vocabulario(self, tp_rbsn: int, vocabulario: str,
                                                     data: date) -> list:
        # TipoReceitaBrutaSimplesRepository.buscarVigentesPorCodigoEVocabulario
        return self._linhas(
            _TIPO_RECEITA,
            f"SELECT {_colunas(_TIPO_RECEITA, 't')} FROM TIPO_RECEITA_BRUTA_SIMPLES_NACIONAL t "
            "WHERE t.TRBS_CD = :codigo "
            "AND (t.TRBS_VOCABULARIO_REGIME = :vocabulario OR t.TRBS_VOCABULARIO_REGIME IS NULL) "
            "AND :data BETWEEN t.TRBS_INICIO_VIGENCIA AND COALESCE(t.TRBS_FIM_VIGENCIA, :data) "
            "ORDER BY CASE WHEN t.TRBS_VOCABULARIO_REGIME IS NULL THEN 1 ELSE 0 END, "
            "t.TRBS_INICIO_VIGENCIA DESC", codigo=tp_rbsn, vocabulario=vocabulario, data=_iso(data))

    def sigla_tipo_dfe_vigente(self, tipo_dfe_id: int, data: date):
        # TipoDfeCalculoSimplesRepository.buscarSiglaVigentePorId (texto ou None)
        linhas = self._consultar(
            "SELECT td.TPDF_SIGLA FROM TIPO_DFE td WHERE td.TPDF_ID = :tipoDfeId "
            "AND :data BETWEEN td.TPDF_INICIO_VIGENCIA AND COALESCE(td.TPDF_FIM_VIGENCIA, :data)",
            tipoDfeId=tipo_dfe_id, data=_iso(data))[1]
        return linhas[0][0] if linhas else None

    def permite_ncm_zero(self, tipo_dfe_id: int, data: date):
        # TipoDfeCalculoSimplesRepository.permiteNcmZeroVigentePorId (bool ou None)
        linhas = self._consultar(
            "SELECT td.TPDF_B_PERMITE_NCM_ZERO FROM TIPO_DFE td WHERE td.TPDF_ID = :tipoDfeId "
            "AND :data BETWEEN td.TPDF_INICIO_VIGENCIA AND COALESCE(td.TPDF_FIM_VIGENCIA, :data)",
            tipoDfeId=tipo_dfe_id, data=_iso(data))[1]
        return None if not linhas or linhas[0][0] is None else bool(linhas[0][0])

    def contar_sem_tratamento_calculadora(self, tp_doc: int, data: date) -> int:
        # TipoDfeCalculoSimplesRepository.contarSemTratamentoCalculadoraVigentes
        return self._contar(
            "SELECT COUNT(1) FROM TIPO_DFE td WHERE td.TPDF_TIPO = :tpDoc "
            "AND td.TPDF_B_POSSUI_TRATAMENTO_CALCULADORA = 0 "
            "AND :data BETWEEN td.TPDF_INICIO_VIGENCIA AND COALESCE(td.TPDF_FIM_VIGENCIA, :data)",
            tpDoc=tp_doc, data=_iso(data))

    def contar_vinculos_tipo_dfe_calculo(self, tp_doc: int, data: date) -> int:
        # TipoDfeCalculoSimplesRepository.contarVinculosVigentes
        return self._contar(
            "SELECT COUNT(1) FROM TIPO_DFE_CALCULO_SIMPLES_NACIONAL t "
            "JOIN TIPO_DFE td ON td.TPDF_ID = t.TDCS_TPDF_ID WHERE td.TPDF_TIPO = :tpDoc "
            "AND :data BETWEEN t.TDCS_INICIO_VIGENCIA AND COALESCE(t.TDCS_FIM_VIGENCIA, :data) "
            "AND :data BETWEEN td.TPDF_INICIO_VIGENCIA AND COALESCE(td.TPDF_FIM_VIGENCIA, :data)",
            tpDoc=tp_doc, data=_iso(data))

    def tipos_dfe_calculo_vigentes(self, tp_doc: int, data: date) -> list:
        # TipoDfeCalculoSimplesRepository.buscarVigentes
        return self._linhas(
            _TIPO_DFE_CALCULO,
            f"SELECT {_colunas(_TIPO_DFE_CALCULO, 't')} FROM TIPO_DFE_CALCULO_SIMPLES_NACIONAL t "
            "JOIN TIPO_DFE td ON td.TPDF_ID = t.TDCS_TPDF_ID WHERE td.TPDF_TIPO = :tpDoc "
            "AND :data BETWEEN t.TDCS_INICIO_VIGENCIA AND COALESCE(t.TDCS_FIM_VIGENCIA, :data) "
            "AND :data BETWEEN td.TPDF_INICIO_VIGENCIA AND COALESCE(td.TPDF_FIM_VIGENCIA, :data) "
            "ORDER BY t.TDCS_INICIO_VIGENCIA DESC", tpDoc=tp_doc, data=_iso(data))

    def atividades_por_c_ativ_sn(self, c_ativ_sn: str, data: date) -> list:
        # AtividadeSimplesCalculoRepository.buscarVigentesPorCAtivSN
        return self._linhas(
            _ATIVIDADE,
            f"SELECT {_colunas(_ATIVIDADE, 'a')} FROM ATIVIDADE_SIMPLES_NACIONAL a "
            "WHERE a.ATSN_CATIVSN = :cAtivSN "
            "AND :data BETWEEN a.ATSN_INICIO_VIGENCIA AND COALESCE(a.ATSN_FIM_VIGENCIA, :data) "
            "ORDER BY a.ATSN_INICIO_VIGENCIA DESC", cAtivSN=c_ativ_sn, data=_iso(data))

    def contar_vinculos_atividade_tp_doc(self, atividade_id: int, tp_doc: int, data: date) -> int:
        # AtividadeSimplesCalculoRepository.contarVinculosComTpDoc
        return self._contar(
            "SELECT COUNT(1) FROM TIPO_DFE_ATIVIDADE_SIMPLES_NACIONAL t "
            "JOIN TIPO_DFE td ON td.TPDF_ID = t.TDAS_TPDF_ID "
            "WHERE t.TDAS_ATSN_ID = :atividadeId AND td.TPDF_TIPO = :tpDoc "
            "AND :data BETWEEN t.TDAS_INICIO_VIGENCIA AND COALESCE(t.TDAS_FIM_VIGENCIA, :data) "
            "AND :data BETWEEN td.TPDF_INICIO_VIGENCIA AND COALESCE(td.TPDF_FIM_VIGENCIA, :data)",
            atividadeId=atividade_id, tpDoc=tp_doc, data=_iso(data))

    # --- repositório do endpoint de atividades ----------------------------------------------

    def atividades_vigentes(self, data: date) -> list:
        # AtividadeSimplesRepository.buscarVigentes
        return self._linhas(
            _ATIVIDADE,
            f"SELECT {_colunas(_ATIVIDADE, 'a')} FROM ATIVIDADE_SIMPLES_NACIONAL a "
            "WHERE :data BETWEEN a.ATSN_INICIO_VIGENCIA AND COALESCE(a.ATSN_FIM_VIGENCIA, :data) "
            "ORDER BY a.ATSN_ORDEM ASC", data=_iso(data))

    def vinculos_tp_doc_vigentes(self, data: date) -> list:
        # AtividadeSimplesRepository.buscarVinculosTpDocVigentes: [{atividadeId, tpDoc}]
        linhas = self._consultar(
            "SELECT t.TDAS_ATSN_ID AS atividadeId, td.TPDF_TIPO AS tpDoc "
            "FROM TIPO_DFE_ATIVIDADE_SIMPLES_NACIONAL t "
            "JOIN TIPO_DFE td ON td.TPDF_ID = t.TDAS_TPDF_ID "
            "AND :data BETWEEN td.TPDF_INICIO_VIGENCIA AND COALESCE(td.TPDF_FIM_VIGENCIA, :data) "
            "WHERE :data BETWEEN t.TDAS_INICIO_VIGENCIA AND COALESCE(t.TDAS_FIM_VIGENCIA, :data)",
            data=_iso(data))[1]
        return [{"atividadeId": a, "tpDoc": t} for a, t in linhas]

    def contar_tipos_dfe_por_tipo(self, tp_doc: int) -> int:
        # AtividadeSimplesRepository.contarTiposDfePorTipo
        return self._contar("SELECT COUNT(1) FROM TIPO_DFE td WHERE td.TPDF_TIPO = :tpDoc",
                            tpDoc=tp_doc)

    # --- endpoints de atividades (AtividadeSimplesController) -------------------------------

    def atividades(self, data, anexo=None, tp_doc=None, c_class_trib=None, *, instancia=None,
                   base_url=None, agora=None):
        """GET calculadora/simples-nacional/atividades -> (status, corpo).

        AtividadeSimplesController.listarAtividades. `data` é `date` ou o texto do parâmetro;
        `anexo` é o parâmetro `anexos` (texto "I,V" ou lista de textos, repetível); `tp_doc` e
        `c_class_trib` são os textos do parâmetro (ou número, no caso do tp_doc). Em sucesso o
        corpo é a lista de AtividadeSimplesDomain. Em erro é o corpo do ApiExceptionHandler.
        `instancia` (caminho pedido), `base_url` (esquema://host do pedido) e `agora` (datetime
        do erro) só entram nos corpos de erro; sem eles valem os padrões deste módulo.
        """
        instancia = CAMINHO_ATIVIDADES if instancia is None else instancia
        try:
            data_ok = _parse_data(data)
            anexos = _converter_anexos(anexo)
            return 200, self._listar(data_ok, anexos, tp_doc, c_class_trib)
        except (_ErroHttp, _ErroTipoArgumento, _ErroParametroAusente, _ErroSistema) as erro:
            return self._corpo_erro(erro, instancia, base_url, agora)

    def atividade(self, codigo: str, data, tp_doc=None, c_class_trib=None, *, instancia=None,
                  base_url=None, agora=None):
        """GET calculadora/simples-nacional/atividades/{codigo} -> (status, corpo).

        AtividadeSimplesController.buscarAtividade. Código sem atividade vigente: (404, None),
        porque a oficial devolve 404 sem corpo. O parâmetro `anexos` não existe neste endpoint.
        """
        if instancia is None:
            instancia = f"{CAMINHO_ATIVIDADES}/{quote(str(codigo), safe=_SEGURO_NO_CAMINHO)}"
        try:
            data_ok = _parse_data(data)
            encontrada = self._buscar_por_codigo(codigo, data_ok, tp_doc, c_class_trib)
        except (_ErroHttp, _ErroTipoArgumento, _ErroParametroAusente, _ErroSistema) as erro:
            return self._corpo_erro(erro, instancia, base_url, agora)
        if encontrada is None:
            return 404, None
        return 200, encontrada

    def _listar(self, data: date, anexos, tp_doc, c_class_trib) -> list:
        # AtividadeSimplesService.listar
        tp_doc_validado = self._validar_tp_doc(tp_doc)
        vedado = self._resolver_vedado_nao_tributavel(c_class_trib, data)
        admissiveis = self._tp_docs_admissiveis(data)
        return [self._para_dominio(a, anexos, admissiveis.get(a["id"], []), tp_doc_validado, vedado)
                for a in self.atividades_vigentes(data)]

    def _buscar_por_codigo(self, codigo, data: date, tp_doc, c_class_trib):
        # AtividadeSimplesService.buscarPorCodigo (valida os filtros mesmo sem achar o código)
        if codigo is None:
            return None
        for item in self._listar(data, None, tp_doc, c_class_trib):
            if item["codigo"] == codigo:
                return item
        return None

    def _validar_tp_doc(self, tp_doc):
        # AtividadeSimplesService.validarTpDoc
        if tp_doc is None:
            return None
        texto = str(tp_doc)
        if texto.strip() == "":
            return None
        informado = texto.strip(_ESPACOS_JAVA)
        # Integer.parseInt: sinal opcional e dígitos, dentro de 32 bits (confirmado na oficial:
        # "+55" e dígitos de outras escritas valem, "5_5" e estouro não).
        if not re.fullmatch(r"[+-]?\d+", informado):
            tipo = None
        else:
            tipo = int(informado)
            if not -2 ** 31 <= tipo <= 2 ** 31 - 1:
                tipo = None
        if tipo is None:
            raise _ErroHttp(400, f"Tipo de documento fiscal inválido: '{informado}'. Informe o valor "
                                 "numérico da coluna TPDF_TIPO da TIPO_DFE (ex.: 55, 65, 91).")
        if tipo < 0 or self.contar_tipos_dfe_por_tipo(tipo) == 0:
            raise _ErroHttp(400, f"Tipo de documento fiscal desconhecido: '{informado}'. Não há "
                                 "linha na TIPO_DFE com TPDF_TIPO correspondente.")
        return str(tipo)

    def _resolver_vedado_nao_tributavel(self, c_class_trib, data: date):
        # AtividadeSimplesService.resolverVedadoNaoTributavel
        if c_class_trib is None or str(c_class_trib).strip() == "":
            return None
        codigo = str(c_class_trib).strip(_ESPACOS_JAVA)
        vigentes = self.classificacao(codigo, data)
        if not vigentes:
            raise _ErroHttp(400, "Código de classificação tributária desconhecido ou fora de "
                                 f"vigência: '{codigo}'. Informe um cClassTrib do grupo da CBS "
                                 "vigente na data de referência.")
        tp_rbsn = vigentes[0]["tpRBSN"]
        tipo = self.tipo_receita_bruta(tp_rbsn, data)
        if tipo is None:
            raise _ErroHttp(400, "Não há parametrização de efeito vigente para o tipo de receita "
                                 f"bruta tpRBSN={tp_rbsn} do cClassTrib '{codigo}' na data de "
                                 "referência.")
        return tipo["vedadoNaoTributavel"]

    def _tp_docs_admissiveis(self, data: date) -> dict:
        # AtividadeSimplesService.buscarTpDocsAdmissiveis: atividadeId -> ["55", "65", ...]
        agrupado = {}
        for vinculo in self.vinculos_tp_doc_vigentes(data):
            agrupado.setdefault(vinculo["atividadeId"], []).append(vinculo["tpDoc"])
        return {chave: [str(t) for t in sorted(tipos)] for chave, tipos in agrupado.items()}

    @staticmethod
    def _para_dominio(atividade, anexos, tp_docs_admissiveis, tp_doc, vedado) -> dict:
        # AtividadeSimplesService.toDomain + AtividadeSimplesDomain (ordem do JsonPropertyOrder;
        # os dois campos opcionais saem só quando o filtro foi informado).
        derivacao = derivar_anexos(atividade["anexoSemFatorR"], atividade["anexoComFatorR"], anexos)
        admissivel = None if tp_doc is None else tp_doc in tp_docs_admissiveis
        compativel = None if vedado is None else not (vedado and atividade["naoTributavel"] is True)
        disponivel = (derivacao["disponivel"] and admissivel is not False and compativel is not False)
        corpo = {
            "codigo": atividade["cAtivSN"],
            "ordem": atividade["ordem"],
            "descricao": atividade["descricao"],
            "anexoSemFatorR": atividade["anexoSemFatorR"],
            "anexoComFatorR": atividade["anexoComFatorR"],
            "sujeitaFatorR": atividade["anexoComFatorR"] is not None,
            "rotuloAnexo": atividade["rotulo"],
            "rotuloCurtoAnexo": atividade["rotuloCurto"],
            "disponivel": disponivel,
            "anexosFaltantes": list(derivacao["anexosFaltantes"]),
            "modoFatorR": derivacao["modoFatorR"],
            "tpDocsAdmissiveis": list(tp_docs_admissiveis),
        }
        if admissivel is not None:
            corpo["admissivelNoDocumento"] = admissivel
        if compativel is not None:
            corpo["compativelComClassTrib"] = compativel
        return corpo

    @staticmethod
    def _corpo_erro(erro, instancia, base_url, agora):
        # ApiExceptionHandler: ResponseStatusException (handleExceptionInternal do Spring),
        # handleMethodArgumentTypeMismatchException e handleMissingServletRequestParameter.
        # Formas conferidas contra a oficial 1.0.2 (chaves na mesma ordem do JSON).
        if isinstance(erro, _ErroHttp):
            return erro.status, {"type": "about:blank", "title": "Bad Request", "status": erro.status,
                                 "detail": erro.detail, "instance": instancia}
        base = BASE_URL_PADRAO if base_url is None else base_url
        if isinstance(erro, _ErroSistema):
            return 500, {"type": f"{base}/errors/erro-de-sistema-nao-previsto",
                         "title": "Erro de sistema n\u00e3o previsto", "status": 500, "detail": erro.detalhe,
                         "instance": instancia, "timestamp": instante(agora)}
        if isinstance(erro, _ErroTipoArgumento):
            titulo, caminho, pointer = "Tipo de argumento inválido", "tipo-argumento-invalido", erro.nome
            detalhe = f"Parâmetro inválido [{erro.nome}: {erro.valor}] "
            detalhe_erro = detalhe.strip()
        else:
            titulo, caminho, pointer = ("Parâmetro obrigatório ausente", "parametro-obrigatorio-ausente",
                                        erro.nome)
            detalhe = f"O parâmetro obrigatório '{erro.nome}' ({erro.tipo}) não foi informado."
            detalhe_erro = detalhe
        return 400, {
            "type": f"{base}/errors/{caminho}", "title": titulo, "status": 400, "detail": detalhe,
            "instance": instancia, "timestamp": instante(agora),
            "errors": [{"title": titulo, "pointer": f"#/{pointer}", "detail": detalhe_erro}],
        }

    # --- nomenclatura (domain/service) -------------------------------------------------------

    def existe_ncm(self, ncm: str, data: date) -> bool:
        # NcmService.existeNcm = NcmRepository.existeNcm
        return self._existe(
            "SELECT EXISTS (SELECT 1 FROM NCM n WHERE n.NCM_CD = :codigo "
            "AND :data BETWEEN n.NCM_INICIO_VIGENCIA AND COALESCE(n.NCM_FIM_VIGENCIA, :data))",
            codigo=ncm, data=_iso(data))

    def existe_nbs(self, nbs: str, data: date) -> bool:
        # NbsService.existeNbs = NbsRepository.existeNbs
        return self._existe(
            "SELECT EXISTS (SELECT 1 FROM NBS n WHERE n.NBS_CD = :codigo "
            "AND :data BETWEEN n.NBS_INICIO_VIGENCIA AND COALESCE(n.NBS_FIM_VIGENCIA, :data))",
            codigo=nbs, data=_iso(data))

    def tem_ncm_aplicavel_para_classificacao(self, id_classificacao: int, data: date) -> bool:
        # NcmAplicavelRepository.tem
        return self._existe(
            "SELECT EXISTS (SELECT 1 FROM NCM_APLICAVEL n WHERE n.NCMA_CLTR_ID = :id "
            "AND :data BETWEEN n.NCMA_INICIO_VIGENCIA AND COALESCE(n.NCMA_FIM_VIGENCIA, :data))",
            id=id_classificacao, data=_iso(data))

    def tem_ncm_aplicavel(self, ncm: str, id_classificacao: int, data: date) -> bool:
        # NcmAplicavelRepository.temNcmAplicavel (o código cadastrado é prefixo do informado)
        return self._existe(
            "SELECT EXISTS (SELECT 1 FROM NCM_APLICAVEL n "
            "WHERE n.NCMA_NCM_CD = SUBSTR(:ncm, 1, LENGTH(n.NCMA_NCM_CD)) AND n.NCMA_CLTR_ID = :id "
            "AND :data BETWEEN n.NCMA_INICIO_VIGENCIA AND COALESCE(n.NCMA_FIM_VIGENCIA, :data))",
            ncm=ncm, id=id_classificacao, data=_iso(data))

    def tem_excecao_ncm_aplicavel(self, ncm: str, id_classificacao: int, data: date) -> bool:
        # NcmAplicavelRepository.temExcecaoNcmAplicavel
        return self._existe(
            "SELECT EXISTS (SELECT 1 FROM EXCECAO_NCM_APLICAVEL e "
            "JOIN NCM_APLICAVEL n ON n.NCMA_ID = e.ENCM_NCMA_ID "
            "WHERE n.NCMA_NCM_CD = SUBSTR(:ncm, 1, LENGTH(n.NCMA_NCM_CD)) "
            "AND e.ENCM_NCM_CD = SUBSTR(:ncm, 1, LENGTH(e.ENCM_NCM_CD)) AND n.NCMA_CLTR_ID = :id "
            "AND :data BETWEEN e.ENCM_INICIO_VIGENCIA AND COALESCE(e.ENCM_FIM_VIGENCIA, :data))",
            ncm=ncm, id=id_classificacao, data=_iso(data))

    def tem_ncm_aplicavel_sem_excecao(self, ncm: str, id_classificacao: int, data: date) -> bool:
        # NcmAplicavelRepository.temNcmAplicavelSemExcecao
        return self._existe(
            "SELECT EXISTS (SELECT 1 FROM NCM_APLICAVEL n "
            "LEFT JOIN EXCECAO_NCM_APLICAVEL e ON e.ENCM_NCMA_ID = n.NCMA_ID "
            "WHERE n.NCMA_NCM_CD = SUBSTR(:ncm, 1, LENGTH(n.NCMA_NCM_CD)) AND n.NCMA_CLTR_ID = :id "
            "AND :data BETWEEN n.NCMA_INICIO_VIGENCIA AND COALESCE(n.NCMA_FIM_VIGENCIA, :data) "
            "AND (e.ENCM_ID IS NULL "
            "OR :data NOT BETWEEN e.ENCM_INICIO_VIGENCIA AND COALESCE(e.ENCM_FIM_VIGENCIA, :data)))",
            ncm=ncm, id=id_classificacao, data=_iso(data))

    def tem_nbs_aplicavel_para_classificacao(self, id_classificacao: int, data: date) -> bool:
        # NbsAplicavelRepository.tem
        return self._existe(
            "SELECT EXISTS (SELECT 1 FROM NBS_APLICAVEL n WHERE n.NBSA_CLTR_ID = :id "
            "AND :data BETWEEN n.NBSA_INICIO_VIGENCIA AND COALESCE(n.NBSA_FIM_VIGENCIA, :data))",
            id=id_classificacao, data=_iso(data))

    def tem_nbs_aplicavel(self, nbs: str, id_classificacao: int, data: date) -> bool:
        # NbsAplicavelRepository.temNbsAplicavel
        return self._existe(
            "SELECT EXISTS (SELECT 1 FROM NBS_APLICAVEL n "
            "WHERE n.NBSA_NBS_CD = SUBSTR(:nbs, 1, LENGTH(n.NBSA_NBS_CD)) AND n.NBSA_CLTR_ID = :id "
            "AND :data BETWEEN n.NBSA_INICIO_VIGENCIA AND COALESCE(n.NBSA_FIM_VIGENCIA, :data))",
            nbs=nbs, id=id_classificacao, data=_iso(data))

    def tem_excecao_nbs_aplicavel(self, nbs: str, id_classificacao: int, data: date) -> bool:
        # NbsAplicavelRepository.temExcecaoNbsAplicavel
        return self._existe(
            "SELECT EXISTS (SELECT 1 FROM EXCECAO_NBS_APLICAVEL e "
            "JOIN NBS_APLICAVEL n ON n.NBSA_ID = e.ENBS_NBSA_ID "
            "WHERE n.NBSA_NBS_CD = SUBSTR(:nbs, 1, LENGTH(n.NBSA_NBS_CD)) "
            "AND e.ENBS_NBS_CD = SUBSTR(:nbs, 1, LENGTH(e.ENBS_NBS_CD)) AND n.NBSA_CLTR_ID = :id "
            "AND :data BETWEEN e.ENBS_INICIO_VIGENCIA AND COALESCE(e.ENBS_FIM_VIGENCIA, :data))",
            nbs=nbs, id=id_classificacao, data=_iso(data))

    def tem_nbs_aplicavel_sem_excecao(self, nbs: str, id_classificacao: int, data: date) -> bool:
        # NbsAplicavelRepository.temNbsAplicavelSemExcecao
        return self._existe(
            "SELECT EXISTS (SELECT 1 FROM NBS_APLICAVEL n "
            "LEFT JOIN EXCECAO_NBS_APLICAVEL e ON e.ENBS_NBSA_ID = n.NBSA_ID "
            "WHERE n.NBSA_NBS_CD = SUBSTR(:nbs, 1, LENGTH(n.NBSA_NBS_CD)) AND n.NBSA_CLTR_ID = :id "
            "AND :data BETWEEN n.NBSA_INICIO_VIGENCIA AND COALESCE(n.NBSA_FIM_VIGENCIA, :data) "
            "AND (e.ENBS_ID IS NULL "
            "OR :data NOT BETWEEN e.ENBS_INICIO_VIGENCIA AND COALESCE(e.ENBS_FIM_VIGENCIA, :data)))",
            nbs=nbs, id=id_classificacao, data=_iso(data))

    def tipo_dfe_classificacao(self, id_classificacao: int, data: date) -> list:
        # TipoDfeClassificacaoRepository.buscar (via TipoDfeClassificacaoService.buscar).
        # Cada item traz o TIPO_DFE em "tipoDfe".
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

    def classificacao_cbs_ibs(self, codigo: str, data: date) -> dict:
        # ClassificacaoTributariaService.buscarClassificacaoTributariaCbsIbs: {id, codigo,
        # nomenclatura, cst}. Sem linha: ClassificacaoTributariaNaoEncontradaException;
        # mais de uma: ResultadoNaoUnico (DataAccessUtils.singleResult).
        linhas = self._consultar(
            "SELECT ct.CLTR_ID, ct.CLTR_CD, ct.CLTR_NOMENCLATURA, st.SITR_CD "
            "FROM CLASSIFICACAO_TRIBUTARIA ct "
            "JOIN SITUACAO_TRIBUTARIA st ON ct.CLTR_SITR_ID = st.SITR_ID "
            "JOIN TRIBUTO_SITUACAO_TRIBUTARIA tst ON tst.TRST_SITR_ID = st.SITR_ID "
            "JOIN TRIBUTO tb ON tst.TRST_TBTO_ID = tb.TBTO_ID "
            "WHERE ct.CLTR_CD = :codigo AND tb.TBTO_ID = 2 "
            "AND :data BETWEEN ct.CLTR_INICIO_VIGENCIA AND COALESCE(ct.CLTR_FIM_VIGENCIA, :data) "
            "AND :data BETWEEN st.SITR_INICIO_VIGENCIA AND COALESCE(st.SITR_FIM_VIGENCIA, :data) "
            "AND :data BETWEEN tst.TRST_INICIO_VIGENCIA AND COALESCE(tst.TRST_FIM_VIGENCIA, :data) "
            "AND :data BETWEEN tb.TBTO_INICIO_VIGENCIA AND COALESCE(tb.TBTO_FIM_VIGENCIA, :data)",
            codigo=codigo, data=_iso(data))[1]
        if not linhas:
            raise ClassificacaoTributariaNaoEncontradaException(codigo, "CBS e IBS", _iso(data))
        if len(linhas) > 1:
            raise ResultadoNaoUnico(
                f"Classificação tributária {codigo}: esperava 1 linha em {_iso(data)}, vieram "
                f"{len(linhas)}")
        i, cd, nomenclatura, cst = linhas[0]
        return {"id": i, "codigo": cd, "nomenclatura": nomenclatura, "cst": cst}

    # --- consultas da sugestão de classificação (não entram em nenhum cálculo do motor) ----------------
    # Origem: scripts/sync-receita.mjs do mini app de imposto por produto da CD2 (MIT), que lê as mesmas
    # tabelas da calculadora do regime regular da Receita. Tributo: 1 Imposto Seletivo, 2 CBS, 3 IBS estadual,
    # 4 IBS municipal.

    def percentuais_reducao(self, id_classificacao: int, data: date) -> dict:
        # sync-receita.mjs, `reductions`: PERCENTUAL_REDUCAO vigente na data (início <= data e fim nulo ou
        # >= data). Devolve {"cbs", "ibsUf", "ibsMun"} em Decimal, 0 quando não há linha. Em caso de duas
        # linhas vigentes para o mesmo tributo vale a de maior PERE_ID.
        tributos = {2: "cbs", 3: "ibsUf", 4: "ibsMun"}
        resultado = {nome: Decimal(0) for nome in tributos.values()}
        linhas = self._consultar(
            "SELECT PERE_TBTO_ID, PERE_VALOR FROM PERCENTUAL_REDUCAO WHERE PERE_CLTR_ID = :id "
            "AND PERE_INICIO_VIGENCIA <= :data AND (PERE_FIM_VIGENCIA IS NULL OR PERE_FIM_VIGENCIA >= :data) "
            "ORDER BY PERE_ID", id=id_classificacao, data=_iso(data))[1]
        for tributo, valor in linhas:
            if tributo in tributos and valor is not None:
                resultado[tributos[tributo]] = _converter(valor, "n")
        return resultado

    def no_imposto_seletivo(self, ncm: str, data: date) -> bool:
        # sync-receita.mjs, `selective`, com a regra de isSelective (classify.ts): o NCM está na lista quando
        # um produto com alíquota de Imposto Seletivo (ad valorem ou ad rem, tributo 1) vigente na data é
        # prefixo do NCM e nenhuma exceção vigente do produto cobre o NCM (ou o prefixo do produto).
        for produto, taxa, chave, excecao in (
                ("ALIQUOTA_AD_VALOREM_PRODUTO", "ALIQUOTA_AD_VALOREM", "AAVP", "EXCECAO_AD_VALOREM_PRODUTO"),
                ("ALIQUOTA_AD_REM_PRODUTO", "ALIQUOTA_AD_REM", "AARP", "EXCECAO_AD_REM_PRODUTO")):
            sigla_taxa = "AADV" if chave == "AAVP" else "AARE"
            sigla_excecao = "EAVP" if chave == "AAVP" else "EARP"
            linhas = self._consultar(
                f"SELECT a.{chave}_ID, a.{chave}_NCM_CD FROM {produto} a "
                f"JOIN {taxa} v ON v.{sigla_taxa}_ID = a.{chave}_{sigla_taxa}_ID "
                f"WHERE v.{sigla_taxa}_TBTO_ID = 1 "
                f"AND a.{chave}_NCM_CD = SUBSTR(:ncm, 1, LENGTH(a.{chave}_NCM_CD)) "
                f"AND :data BETWEEN a.{chave}_INICIO_VIGENCIA AND COALESCE(a.{chave}_FIM_VIGENCIA, :data)",
                ncm=ncm, data=_iso(data))[1]
            for id_produto, ncm_produto in linhas:
                excecoes = self._consultar(
                    f"SELECT {sigla_excecao}_NCM_CD FROM {excecao} WHERE {sigla_excecao}_{chave}_ID = :id "
                    f"AND :data BETWEEN {sigla_excecao}_INICIO_VIGENCIA "
                    f"AND COALESCE({sigla_excecao}_FIM_VIGENCIA, :data)", id=id_produto, data=_iso(data))[1]
                if not any(ncm.startswith(e[0]) or ncm_produto.startswith(e[0]) for e in excecoes):
                    return True
        return False

    def determinar_nomenclatura(self, sigla_dfe: str, c_class_trib: str, data: date) -> str:
        # NomenclaturaService.determinarNomenclatura: SEM, NCM, NBS, MISTO, EXCECAO_NCM ou EXCECAO_NBS.
        classificacao = self.classificacao_cbs_ibs(c_class_trib, data)
        id_classificacao = classificacao["id"]
        tipos = self.tipo_dfe_classificacao(id_classificacao, data)
        normalizada = sigla_dfe_por_normalizada(sigla_dfe)  # valida a sigla informada
        if not any(normalizar_sigla(t["tipoDfe"]["sigla"]) == normalizada for t in tipos):
            return "SEM"
        ncm_aplicavel = self.tem_ncm_aplicavel_para_classificacao(id_classificacao, data)
        nbs_aplicavel = self.tem_nbs_aplicavel_para_classificacao(id_classificacao, data)
        return self._determinar_resultado(sigla_dfe, c_class_trib, ncm_aplicavel, nbs_aplicavel)

    @staticmethod
    def _determinar_resultado(sigla_dfe, c_class_trib, ncm_aplicavel, nbs_aplicavel) -> str:
        # NomenclaturaService.determinarResultado e tratarCasoExcecao
        sigla = normalizar_sigla(sigla_dfe)
        if c_class_trib in _EXCEPTION_CLASSIFICACOES:
            normalizada = sigla_dfe_por_normalizada(sigla_dfe)
            if normalizada in ("NFE", "NFCE"):
                return "EXCECAO_NCM"
            if normalizada == "NFSE":
                return "EXCECAO_NBS"
            return "SEM"
        if sigla in _NCM_APLICAVEL and sigla not in _NBS_APLICAVEL:
            return "NCM" if ncm_aplicavel else "SEM"
        if sigla in _NBS_APLICAVEL:
            if ncm_aplicavel and nbs_aplicavel:
                return "MISTO"
            if ncm_aplicavel:
                return "NCM"
            if nbs_aplicavel:
                return "NBS"
            return "SEM"
        return "SEM"

    def validar_ncm(self, ncm, id_classificacao: int, codigo_classificacao: str, data: date,
                    tributos: str) -> bool:
        # NcmAplicavelService.validarNcmAplicavel (sempre True; erro vira exceção)
        completo = _tamanho(ncm) == 8
        tem_classificacao = self.tem_ncm_aplicavel_para_classificacao(id_classificacao, data)
        if tem_classificacao and not completo:
            raise NcmCompletoNaoInformadoException()
        if completo:
            self._criticar_ncm(ncm, id_classificacao, codigo_classificacao, data, tributos,
                               tem_classificacao)
        return True

    def validar_nbs(self, nbs, id_classificacao: int, codigo_classificacao: str, data: date,
                    tributos: str) -> bool:
        # NbsAplicavelService.validarNbsAplicavel (sempre True; erro vira exceção)
        completo = _tamanho(nbs) == 9
        tem_classificacao = self.tem_nbs_aplicavel_para_classificacao(id_classificacao, data)
        if tem_classificacao and not completo:
            raise NbsCompletoNaoInformadoException()
        if completo:
            self._criticar_nbs(nbs, id_classificacao, codigo_classificacao, data, tributos,
                               tem_classificacao)
        return True

    def criticar_ncm_contra_anexo(self, ncm, id_classificacao, codigo_classificacao, data,
                                  tributos) -> None:
        # NcmAplicavelService.criticarNcmContraAnexo
        self._criticar_ncm(ncm, id_classificacao, codigo_classificacao, data, tributos,
                           self.tem_ncm_aplicavel_para_classificacao(id_classificacao, data))

    def criticar_nbs_contra_anexo(self, nbs, id_classificacao, codigo_classificacao, data,
                                  tributos) -> None:
        # NbsAplicavelService.criticarNbsContraAnexo
        self._criticar_nbs(nbs, id_classificacao, codigo_classificacao, data, tributos,
                           self.tem_nbs_aplicavel_para_classificacao(id_classificacao, data))

    def _criticar_ncm(self, ncm, id_classificacao, codigo, data, tributos, tem_classificacao):
        # NcmAplicavelService.criticar. A = tem_ncm_aplicavel, E = tem_excecao_ncm_aplicavel,
        # S = tem_ncm_aplicavel_sem_excecao: rejeita quando !A, ou quando E e !S.
        if not tem_classificacao:
            return  # classificação sem anexo: aceita sem crítica
        if not self.tem_ncm_aplicavel(ncm, id_classificacao, data):
            raise NcmNaoVinculadaException(ncm, codigo, tributos)
        if (self.tem_excecao_ncm_aplicavel(ncm, id_classificacao, data)
                and not self.tem_ncm_aplicavel_sem_excecao(ncm, id_classificacao, data)):
            raise NcmNaoVinculadaException(ncm, codigo, tributos)

    def _criticar_nbs(self, nbs, id_classificacao, codigo, data, tributos, tem_classificacao):
        # NbsAplicavelService.criticar (mesma tabela-verdade do NCM)
        if not tem_classificacao:
            return
        if not self.tem_nbs_aplicavel(nbs, id_classificacao, data):
            raise NbsNaoVinculadaException(nbs, codigo, tributos)
        if (self.tem_excecao_nbs_aplicavel(nbs, id_classificacao, data)
                and not self.tem_nbs_aplicavel_sem_excecao(nbs, id_classificacao, data)):
            raise NbsNaoVinculadaException(nbs, codigo, tributos)

    def validar_nomenclatura(self, sigla_dfe: str, id_classificacao: int, codigo_classificacao: str,
                             ncm, nbs, data: date, tributos: str, modo: str = MODO_PERMISSIVO) -> None:
        # ValidacaoNomenclaturaService.validar. `sigla_dfe` é a sigla do documento (a oficial já
        # a recebe como SiglasDFeEnum; aqui ela é normalizada e a inválida levanta
        # SiglaDFeNaoEncontradaException). Levanta a exceção da primeira crítica que falha.
        sigla = sigla_dfe_por_normalizada(sigla_dfe)
        resultado = self.determinar_nomenclatura(sigla, codigo_classificacao, data)
        args = (sigla, id_classificacao, codigo_classificacao, ncm, nbs, data, tributos)
        if resultado in ("NCM", "EXCECAO_NCM"):
            self._exigir_ncm(*args)
        elif resultado in ("NBS", "EXCECAO_NBS"):
            self._exigir_nbs(*args)
        elif resultado == "MISTO":
            self._exigir_ncm_ou_nbs(*args)
        else:
            self._validar_sem_exigencia(*args, modo)

    def _exigir_ncm(self, sigla, id_classificacao, codigo, ncm, nbs, data, tributos):
        if _preenchido(nbs):
            raise NomenclaturaIncompativelTipoDfeException("NBS", codigo, SIGLAS_DFE[sigla])
        if _tamanho(ncm) != 8:
            raise NcmCompletoNaoInformadoException()
        self.criticar_ncm_contra_anexo(ncm, id_classificacao, codigo, data, tributos)

    def _exigir_nbs(self, sigla, id_classificacao, codigo, ncm, nbs, data, tributos):
        if _preenchido(ncm):
            raise NomenclaturaIncompativelTipoDfeException("NCM", codigo, SIGLAS_DFE[sigla])
        if _tamanho(nbs) != 9:
            raise NbsCompletoNaoInformadoException()
        self.criticar_nbs_contra_anexo(nbs, id_classificacao, codigo, data, tributos)

    def _exigir_ncm_ou_nbs(self, sigla, id_classificacao, codigo, ncm, nbs, data, tributos):
        # A informação simultânea de NCM e NBS é rejeitada antes, na validação estrutural do item.
        if _preenchido(ncm):
            if _tamanho(ncm) != 8:
                raise NcmCompletoNaoInformadoException()
            self.criticar_ncm_contra_anexo(ncm, id_classificacao, codigo, data, tributos)
        elif _preenchido(nbs):
            if _tamanho(nbs) != 9:
                raise NbsCompletoNaoInformadoException()
            self.criticar_nbs_contra_anexo(nbs, id_classificacao, codigo, data, tributos)
        else:
            raise NomenclaturaObrigatoriaNaoInformadaException(codigo, SIGLAS_DFE[sigla])

    def _validar_sem_exigencia(self, sigla, id_classificacao, codigo, ncm, nbs, data, tributos, modo):
        ncm_informado = _preenchido(ncm)
        nbs_informado = _preenchido(nbs)
        if not ncm_informado and not nbs_informado:
            return
        if sigla in _TRIO_RIGIDO:
            # Dentro do trio a regra é sempre rígida: só a nomenclatura natural do documento.
            if sigla == "NFSE":
                if ncm_informado:
                    raise NomenclaturaIncompativelTipoDfeException("NCM", codigo, SIGLAS_DFE[sigla])
            elif nbs_informado:
                raise NomenclaturaIncompativelTipoDfeException("NBS", codigo, SIGLAS_DFE[sigla])
            return
        if modo == MODO_RIGIDO:
            raise NomenclaturaNaoPermitidaTipoDfeException(codigo, SIGLAS_DFE[sigla])
        # PERMISSIVO: critica o valor contra o anexo, se a classificação tiver um.
        if ncm_informado:
            self.validar_ncm(ncm, id_classificacao, codigo, data, tributos)
        else:
            self.validar_nbs(nbs, id_classificacao, codigo, data, tributos)


def instante(agora) -> str:
    """Instant.now() como o Jackson escreve (ISO_INSTANT: fração em 0, 3, 6 ou 9 dígitos)."""
    if agora is None:
        agora = datetime.now(timezone.utc)
    if agora.tzinfo is not None:
        agora = agora.astimezone(timezone.utc)
    texto = agora.strftime("%Y-%m-%dT%H:%M:%S")
    micro = agora.microsecond
    if micro:
        texto += f".{micro:06d}" if micro % 1000 else f".{micro // 1000:03d}"
    return texto + "Z"
