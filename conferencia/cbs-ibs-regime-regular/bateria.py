"""Bateria de conferência da skill cbs-ibs-regime-regular.

Gera da base da Receita a lista de requisições que a conferência faz à calculadora oficial do regime
regular (os casos escritos dos testes da Receita e a matriz tirada das tabelas) e responde cada uma com a
conta local.

Exporta, para scripts/receita_paridade.py e para os testes:
    casos(base_completa=None, semente=7)   lista de casos, sempre na mesma ordem
    amostra(casos, n)                       n casos: todos os escritos e os gerados espalhados pelos grupos
    responder(requisicao)                   {"status", "corpo"} da conta local (rr_calc.executar)
    omitidos(base_completa=None, semente=7) o que a base pede e a matriz não gerou, com o motivo
    GRUPOS                                  os grupos fixos, que os testes das Tasks 5 a 10 filtram

Cada caso: {"id", "grupo", "origem", "requisicao": {"metodo", "caminho", "corpo"}} e, nos gerados, "chave"
(o que o caso cobre, para a amostra espalhar). Os escritos trazem também "fonte" (o teste da Receita).

O grupo diz até onde o motor precisa chegar para responder o caso:
    entrada             a requisição para antes dos itens: JSON ilegível, Bean Validation (VAL-*), data,
                        UF e município, tipo de documento
    nomenclatura        um item que o processamento recusa: NCM, CST, cClassTrib, vínculos, anexos,
                        tributação regular ausente, alíquotas nominais (REG-036 e REG-037), base de cálculo
    cbs-ibs             um item calculado sem redução, diferimento nem tributação regular (e o CAL-007 das
                        classificações sem tratamento)
    reducao             um item calculado com gRed
    diferimento         um item calculado com gDif
    tributacao-regular  um item com o grupo de tributação regular (desoneração)
    is                  um item com Imposto Seletivo: calculado, sem o grupo (REG-013) ou sem alíquota (REG-035)
    memoria             a memória de cálculo, com quantidades que o BigDecimal.toString() do Java escreve em
                        notação científica
    totais              vários itens no /observabilidade, misturando válidos e inválidos, ou sem nenhum válido
    regime-geral        vários itens no /regime-geral: todos válidos (o lote do teste de arredondamento) ou
                        com exatamente um inválido (com mais de um o Java devolve um erro qualquer, porque
                        processa os itens em parallelStream)

Fora da bateria, porque a skill recusa antes do motor: NBS, NFS-e, tpDoc que não seja 55 nem 65, e
gCompraGov. Os testes da Receita que só cobrem isso estão em `excluidos_da_bateria` do config.json.

Nada aqui usa a data de hoje nem sorteio sem semente: a mesma base e a mesma semente dão a mesma lista.
`casos` lê só tabelas que a base reduzida da skill leva, então `base_completa` pode ser omitida.
"""

from __future__ import annotations

import copy
import importlib.util
import math
import random
import sqlite3
import sys
from decimal import ROUND_FLOOR, Decimal, localcontext
from pathlib import Path

PASTA = Path(__file__).resolve().parent
RAIZ = PASTA.parent.parent
SKILL = RAIZ / "core" / "cd2-super" / "skills" / "cbs-ibs-regime-regular"
BASE_DA_SKILL = SKILL / "references" / "base-receita.db"
SEMENTE_PADRAO = 7
MINIMO_DE_GERADOS_POR_GRUPO = 6  # na amostra, antes de equilibrar os grupos
CAMINHO_REGIME_GERAL = "/calculadora/regime-geral"
CAMINHO_OBSERVABILIDADE = "/calculadora/observabilidade/regime-geral"
GRUPOS = ("entrada", "nomenclatura", "cbs-ibs", "reducao", "diferimento", "tributacao-regular", "is", "memoria",
          "totais", "regime-geral")

# As duas datas da matriz: o último dia com as alíquotas da base e o primeiro com as alíquotas informadas.
DATA_2026 = "2026-12-31"
DATA_2027 = "2027-01-01"
ANO_DAS_ALIQUOTAS_INFORMADAS = 2027
# O cenário "Em 2027" da skill (calc-imposto e docs/exemplos.md da Receita): CBS 11,3%, IBS 0,05% + 0,05%.
CENARIO_EM_2027 = {"cbs": Decimal("11.3"), "ibsEstadual": Decimal("0.05"), "ibsMunicipal": Decimal("0.05")}
# Alíquotas do IS de exemplo: a base traz o registro sem valor, e a partir de 2027 elas vêm na requisição.
IS_AD_VALOREM = Decimal("13")
IS_AD_REM = Decimal("21.3")
TP_DOC_DA_SIGLA = {"NF-e": 55, "NFC-e": 65}
UFS_DOS_LOCAIS = ("RS", "SP", "AM", "AC", "DF")
# Quantidades que o BigDecimal.toString() do Java escreve em notação científica (1E+3, 1.5E+2) ou com zeros
# que o stripTrailingZeros tira (100, 2.50), mais o máximo de casas e de dígitos que o contrato aceita.
QUANTIDADES_DA_MEMORIA = ("1E+3", "1.5E+2", "100", "2.50", "0.0001", "12345678901.1234", "3.1415")


def _carregar(nome: str, caminho: Path):
    if nome in sys.modules:
        return sys.modules[nome]
    spec = importlib.util.spec_from_file_location(nome, caminho)
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[nome] = modulo
    try:
        spec.loader.exec_module(modulo)
    except BaseException:
        sys.modules.pop(nome, None)
        raise
    return modulo


receita_paridade = _carregar("receita_paridade", RAIZ / "scripts" / "receita_paridade.py")


# --- a conta local -------------------------------------------------------------------------------

def responder(requisicao: dict) -> dict:
    """A resposta da conta local ({"status", "corpo"}), com as mesmas convenções das oficiais gravadas.

    O calc.py da skill é carregado só aqui (como `rr_calc`), para a bateria importar antes de ele existir.
    """
    rr_calc = _carregar("rr_calc", SKILL / "scripts" / "calc.py")
    return rr_calc.executar(copy.deepcopy(requisicao))


# --- a base --------------------------------------------------------------------------------------------

def _vigente(inicio, fim, data: str) -> bool:
    return str(inicio)[:10] <= data and (fim is None or str(fim)[:10] >= data)


class _Dados:
    """O que a matriz lê da base: classificações, vínculos com NF-e e NFC-e, anexos de NCM e o IS."""

    def __init__(self, caminho: Path):
        con = sqlite3.connect(f"{Path(caminho).resolve().as_uri()}?mode=ro", uri=True)
        try:
            self._ler(con)
        finally:
            con.close()

    def _ler(self, con):
        sql = lambda texto, *p: con.execute(texto, p).fetchall()  # noqa: E731
        self.situacoes = [  # (código, início, fim, exige o grupo IBS/CBS, gRed, gDif, tributos)
            {"cst": r[0], "inicio": r[1], "fim": r[2], "grupo": r[3] == 1, "gred": r[4] == 1, "gdif": r[5] == 1,
             "tributos": {int(t) for t in (r[6] or "").split(",") if t}}
            for r in sql("SELECT s.SITR_CD, s.SITR_INICIO_VIGENCIA, s.SITR_FIM_VIGENCIA, s.SITR_IND_GIBSCBS, "
                         "s.SITR_IND_GRED, s.SITR_IND_GDIF, group_concat(t.TRST_TBTO_ID) "
                         "FROM SITUACAO_TRIBUTARIA s LEFT JOIN TRIBUTO_SITUACAO_TRIBUTARIA t ON t.TRST_SITR_ID = s.SITR_ID "
                         "GROUP BY s.SITR_ID ORDER BY s.SITR_ID")]
        self.classes = [
            {"id": r[0], "codigo": r[1], "cst": r[2], "inicio": r[3], "fim": r[4], "tributos": {int(t) for t in
             (r[5] or "").split(",") if t}, "desoneracao": r[6] == 1, "reducao": r[7] == 1, "ajuste": r[8] == 1}
            for r in sql("SELECT c.CLTR_ID, c.CLTR_CD, s.SITR_CD, c.CLTR_INICIO_VIGENCIA, c.CLTR_FIM_VIGENCIA, "
                         "(SELECT group_concat(t.TRST_TBTO_ID) FROM TRIBUTO_SITUACAO_TRIBUTARIA t "
                         " WHERE t.TRST_SITR_ID = s.SITR_ID), "
                         "EXISTS (SELECT 1 FROM TRATAMENTO_CLASSIFICACAO tc JOIN TRATAMENTO_TRIBUTARIO tt "
                         " ON tt.TRTR_ID = tc.TRCL_TRTR_ID WHERE tc.TRCL_CLTR_ID = c.CLTR_ID "
                         " AND tt.TRTR_IN_EXIGE_GRUPO_DESONERACAO = 1), "
                         "EXISTS (SELECT 1 FROM PERCENTUAL_REDUCAO p WHERE p.PERE_CLTR_ID = c.CLTR_ID AND p.PERE_VALOR > 0), "
                         "EXISTS (SELECT 1 FROM TRATAMENTO_CLASSIFICACAO tc JOIN TRATAMENTO_TRIBUTARIO tt "
                         " ON tt.TRTR_ID = tc.TRCL_TRTR_ID WHERE tc.TRCL_CLTR_ID = c.CLTR_ID "
                         " AND tt.TRTR_IN_POSSUI_AJUSTE = 1) "
                         "FROM CLASSIFICACAO_TRIBUTARIA c JOIN SITUACAO_TRIBUTARIA s ON s.SITR_ID = c.CLTR_SITR_ID "
                         "ORDER BY c.CLTR_ID")]
        self.docs_da_classe = {}  # CLTR_ID -> [(tpDoc, início, fim)], só NF-e e NFC-e
        for cltr, sigla, inicio, fim in sql(
                "SELECT t.TDCL_CLTR_ID, d.TPDF_SIGLA, t.TDCL_INICIO_VIGENCIA, t.TDCL_FIM_VIGENCIA "
                "FROM TIPO_DFE_CLASSIFICACAO t JOIN TIPO_DFE d ON d.TPDF_ID = t.TDCL_TPDF_ID ORDER BY t.TDCL_ID"):
            if sigla in TP_DOC_DA_SIGLA:
                self.docs_da_classe.setdefault(cltr, []).append((TP_DOC_DA_SIGLA[sigla], inicio, fim))
        self.anexo = {}  # CLTR_ID -> [(NCMA_ID, prefixo, início, fim)] na ordem da tabela
        for ncma, cltr, codigo, inicio, fim in sql("SELECT NCMA_ID, NCMA_CLTR_ID, NCMA_NCM_CD, NCMA_INICIO_VIGENCIA, "
                                                  "NCMA_FIM_VIGENCIA FROM NCM_APLICAVEL ORDER BY NCMA_ID"):
            self.anexo.setdefault(cltr, []).append((ncma, codigo, inicio, fim))
        self.excecoes = {}  # NCMA_ID -> [(prefixo, início, fim)]
        for ncma, codigo, inicio, fim in sql("SELECT ENCM_NCMA_ID, ENCM_NCM_CD, ENCM_INICIO_VIGENCIA, ENCM_FIM_VIGENCIA "
                                            "FROM EXCECAO_NCM_APLICAVEL ORDER BY ENCM_ID"):
            self.excecoes.setdefault(ncma, []).append((codigo, inicio, fim))
        self.ncms = [(r[0], r[1], r[2]) for r in sql("SELECT NCM_CD, NCM_INICIO_VIGENCIA, NCM_FIM_VIGENCIA FROM NCM "
                                                       "WHERE length(NCM_CD) = 8 ORDER BY NCM_CD")]
        # IS (tributo 1): produtos com ad valorem e com ad rem, e as exceções, como nos repositórios do Java
        self.is_ad_valorem = [(r[0], r[1], r[2], r[3]) for r in sql(
            "SELECT p.AAVP_ID, p.AAVP_NCM_CD, p.AAVP_INICIO_VIGENCIA, p.AAVP_FIM_VIGENCIA "
            "FROM ALIQUOTA_AD_VALOREM_PRODUTO p JOIN ALIQUOTA_AD_VALOREM a ON a.AADV_ID = p.AAVP_AADV_ID "
            "WHERE a.AADV_TBTO_ID = 1 ORDER BY p.AAVP_ID")]
        self.is_ad_rem = [(r[0], r[1], r[2], r[3], r[4]) for r in sql(
            "SELECT p.AARP_ID, p.AARP_NCM_CD, p.AARP_INICIO_VIGENCIA, p.AARP_FIM_VIGENCIA, u.UNMD_SIGLA "
            "FROM ALIQUOTA_AD_REM_PRODUTO p JOIN ALIQUOTA_AD_REM a ON a.AARE_ID = p.AARP_AARE_ID "
            "JOIN UNIDADE_MEDIDA u ON u.UNMD_ID = a.AARE_UNMD_ID WHERE a.AARE_TBTO_ID = 1 ORDER BY p.AARP_ID")]
        self.excecoes_ad_valorem = {}
        for aavp, codigo, inicio, fim in sql("SELECT EAVP_AAVP_ID, EAVP_NCM_CD, EAVP_INICIO_VIGENCIA, EAVP_FIM_VIGENCIA "
                                            "FROM EXCECAO_AD_VALOREM_PRODUTO ORDER BY EAVP_ID"):
            self.excecoes_ad_valorem.setdefault(aavp, []).append((codigo, inicio, fim))
        self.excecoes_ad_rem = {}
        for aarp, codigo, inicio, fim in sql("SELECT EARP_AARP_ID, EARP_NCM_CD, EARP_INICIO_VIGENCIA, EARP_FIM_VIGENCIA "
                                            "FROM EXCECAO_AD_REM_PRODUTO ORDER BY EARP_ID"):
            self.excecoes_ad_rem.setdefault(aarp, []).append((codigo, inicio, fim))
        self.locais = []
        for uf in UFS_DOS_LOCAIS:
            linha = con.execute("SELECT m.MUNI_CD FROM MUNICIPIO m JOIN UF u ON u.UF_CD = m.MUNI_UF_CD "
                                "WHERE u.UF_SIGLA = ? AND m.MUNI_FIM_VIGENCIA IS NULL ORDER BY m.MUNI_CD LIMIT 1",
                                (uf,)).fetchone()
            if linha:
                self.locais.append((linha[0], uf))

    # --- consultas --------------------------------------------------------------------------------------

    def situacao(self, cst: str, tributo: int, data: str):
        for s in self.situacoes:
            if s["cst"] == cst and tributo in s["tributos"] and _vigente(s["inicio"], s["fim"], data):
                return s
        return None

    def classes_cbs(self, data: str) -> list:
        """As classificações da CBS e do IBS vigentes na data (uma por código, a de início mais recente)."""
        por_codigo = {}
        for c in self.classes:
            if 2 in c["tributos"] and _vigente(c["inicio"], c["fim"], data):
                atual = por_codigo.get(c["codigo"])
                if atual is None or str(c["inicio"]) > str(atual["inicio"]):
                    por_codigo[c["codigo"]] = c
        return sorted(por_codigo.values(), key=lambda c: c["codigo"])

    def classes_is(self, data: str) -> list:
        return [c for c in self.classes if 1 in c["tributos"] and _vigente(c["inicio"], c["fim"], data)]

    def documentos(self, classe, data: str) -> list:
        return sorted({tp for tp, i, f in self.docs_da_classe.get(classe["id"], []) if _vigente(i, f, data)})

    def ncm_existe(self, ncm: str, data: str) -> bool:
        return any(c == ncm and _vigente(i, f, data) for c, i, f in self.ncms)

    def _ncms_com_prefixo(self, prefixo: str, data: str):
        return [c for c, i, f in self.ncms if c.startswith(prefixo) and _vigente(i, f, data)]

    def tem_is(self, ncm: str, data: str) -> bool:
        """Como existeIncidenciaImpostoSeletivo: registro de ad valorem ou ad rem do IS vigente, sem exceção."""
        if int(data[:4]) < ANO_DAS_ALIQUOTAS_INFORMADAS or not self.ncm_existe(ncm, data):
            return False
        return self.ad_valorem(ncm, data) or self.ad_rem(ncm, data) is not None

    def ad_valorem(self, ncm: str, data: str) -> bool:
        return any(ncm.startswith(c) and _vigente(i, f, data) and not self._excecao(
            self.excecoes_ad_valorem.get(aavp, []), ncm, c, data) for aavp, c, i, f in self.is_ad_valorem)

    def ad_rem(self, ncm: str, data: str):
        """A unidade do ad rem do IS do NCM na data, ou None."""
        candidatos = [(len(c), un) for aarp, c, i, f, un in self.is_ad_rem
                      if ncm.startswith(c) and _vigente(i, f, data)
                      and not self._excecao(self.excecoes_ad_rem.get(aarp, []), ncm, c, data)]
        return max(candidatos)[1] if candidatos else None

    @staticmethod
    def _excecao(excecoes, ncm: str, prefixo_produto: str, data: str) -> bool:
        return any((ncm.startswith(c) or prefixo_produto.startswith(c)) and _vigente(i, f, data)
                   for c, i, f in excecoes)

    def ncm_do_anexo(self, classe, data: str):
        """O primeiro NCM completo do anexo da classificação (pela ordem da tabela), sem exceção e sem IS.

        None quando a classificação não tem anexo vigente; "" quando tem e nenhum NCM serve.
        """
        linhas = [(ncma, c) for ncma, c, i, f in self.anexo.get(classe["id"], []) if _vigente(i, f, data)]
        if not linhas:
            return None
        for ncma, prefixo in linhas:
            for ncm in self._ncms_com_prefixo(prefixo, data):
                if any(ncm.startswith(e) and _vigente(i, f, data) for e, i, f in self.excecoes.get(ncma, [])):
                    continue
                if not self.tem_is(ncm, data):
                    return ncm
        return ""

    def ncm_fora_do_anexo(self, classe, data: str):
        """O primeiro NCM completo que nenhuma linha do anexo cobre (e sem IS), ou None sem anexo."""
        linhas = [c for ncma, c, i, f in self.anexo.get(classe["id"], []) if _vigente(i, f, data)]
        if not linhas:
            return None
        return next((c for c, i, f in self.ncms if _vigente(i, f, data) and not any(c.startswith(p) for p in linhas)
                     and not self.tem_is(c, data)), None)

    def ncm_generico(self, data: str) -> str:
        """O primeiro NCM completo vigente sem IS: o das classificações sem anexo."""
        return next(c for c, i, f in self.ncms if _vigente(i, f, data) and not self.tem_is(c, data))

    def ncms_com_is(self, data: str) -> list:
        """Um NCM completo por linha de produto do IS (ad valorem e ad rem), sem repetir, na ordem da tabela."""
        saida = []
        for _, prefixo, i, f in self.is_ad_valorem + [linha[:4] for linha in self.is_ad_rem]:
            if not _vigente(i, f, data):
                continue
            ncm = next((n for n in self._ncms_com_prefixo(prefixo, data) if self.tem_is(n, data)), None)
            if ncm and ncm not in saida:
                saida.append(ncm)
        return saida

    def classe_is_do_ncm(self, ncm: str, data: str):
        """A classificação do IS cujo anexo cobre o NCM (a primeira pela ordem), ou a primeira do IS."""
        classes = self.classes_is(data)
        for c in classes:
            if any(ncm.startswith(p) and _vigente(i, f, data) for _, p, i, f in self.anexo.get(c["id"], [])):
                return c
        return classes[0] if classes else None


# --- montagem das requisições ------------------------------------------------------------------------------

def _requisicao(corpo, caminho: str = CAMINHO_OBSERVABILIDADE) -> dict:
    return {"metodo": "POST", "caminho": caminho, "corpo": corpo}


def _operacao(data: str, itens: list, local=(4314902, "RS"), tp_doc=None, id_="bateria") -> dict:
    corpo = {"id": id_, "versao": "0.0.1", "dhFatoGerador": f"{data}T10:00:00-03:00",
             "municipio": local[0], "uf": local[1]}
    if tp_doc is not None:
        corpo["tpDoc"] = tp_doc
    corpo["itens"] = itens
    return corpo


def _base_calculo(rng: random.Random) -> Decimal:
    return Decimal(rng.randint(1, 10 ** rng.randint(3, 9))).scaleb(-2)


class _Gerador:
    def __init__(self, dados: _Dados, semente: int):
        self.d = dados
        self.rng = random.Random(semente)
        self.casos: list = []
        self.omitidos: list = []
        self._ids: set = set()

    def adicionar(self, grupo: str, chave: str, corpo, caminho: str = CAMINHO_OBSERVABILIDADE):
        assert grupo in GRUPOS, grupo
        sufixo = "rg" if caminho == CAMINHO_REGIME_GERAL else "obs"
        base = f"{grupo}-{chave}-{sufixo}"
        id_, n = base, 2
        while id_ in self._ids:
            id_, n = f"{base}-{n}", n + 1
        self._ids.add(id_)
        self.casos.append({"id": id_, "grupo": grupo, "origem": "gerado", "chave": f"{grupo}:{chave}",
                           "requisicao": _requisicao(copy.deepcopy(corpo), caminho)})

    def omitir(self, grupo: str, chave: str, motivo: str):
        self.omitidos.append({"grupo": grupo, "chave": chave, "motivo": motivo})

    def local(self):
        return self.rng.choice(self.d.locais)

    def item(self, classe, ncm, data: str, numero: int = 1, aliquotas=True) -> dict:
        """Um item válido da classificação na data (alíquotas do cenário em 2027 quando o CST exige o grupo)."""
        item = {"numero": numero}
        if ncm:
            item["ncm"] = ncm
        item.update({"cst": classe["cst"], "cClassTrib": classe["codigo"], "baseCalculo": _base_calculo(self.rng),
                     "quantidade": 1, "unidade": "UN"})
        if classe["desoneracao"]:
            item["tributacaoRegular"] = {"cst": "000", "cClassTrib": "000001"}
        situacao = self.d.situacao(classe["cst"], 2, data)
        if aliquotas and int(data[:4]) >= ANO_DAS_ALIQUOTAS_INFORMADAS and situacao and situacao["grupo"]:
            item["aliquotasNominais"] = dict(CENARIO_EM_2027)
        return item

    def ncm_da_classe(self, classe, data: str):
        ncm = self.d.ncm_do_anexo(classe, data)
        if ncm is None:
            return self.d.ncm_generico(data)
        return ncm or None

    def grupo_da_classe(self, classe, data: str) -> str:
        situacao = self.d.situacao(classe["cst"], 2, data) or {}
        if classe["desoneracao"]:
            return "tributacao-regular"
        if situacao.get("gdif"):
            return "diferimento"
        if situacao.get("gred") or classe["reducao"]:
            return "reducao"
        return "cbs-ibs"


# --- a matriz das classificações ---------------------------------------------------------------------------

def _matriz_de_classificacoes(g: _Gerador) -> None:
    """Cada cClassTrib vinculada a NF-e ou NFC-e, com o primeiro NCM do anexo, nas duas datas e sem tpDoc."""
    for data in (DATA_2027, DATA_2026):
        for classe in g.d.classes_cbs(data):
            docs = g.d.documentos(classe, data)
            if not docs:
                continue
            ncm = g.ncm_da_classe(classe, data)
            chave = f"{classe['codigo']}-{data}"
            if ncm is None:
                g.omitir("cbs-ibs", chave, "o anexo da classificação não tem NCM completo vigente sem exceção e sem IS")
                continue
            grupo = g.grupo_da_classe(classe, data)
            corpo = _operacao(data, [g.item(classe, ncm, data)], g.local(), docs[0])
            g.adicionar(grupo, chave, corpo)
            if data == DATA_2027:
                g.adicionar(grupo, chave, corpo, CAMINHO_REGIME_GERAL)
                sem_doc = _operacao(data, [g.item(classe, ncm, data)], g.local())
                g.adicionar(grupo, chave + "-sem-tpdoc", sem_doc)
                if len(docs) > 1:
                    g.adicionar(grupo, chave + f"-tpdoc-{docs[1]}",
                                _operacao(data, [g.item(classe, ncm, data)], g.local(), docs[1]))


def _erros_de_nomenclatura(g: _Gerador) -> None:
    """Um caso de erro por CST, por anexo, por vínculo, por grupo de tributação regular e pelas alíquotas."""
    d = g.d
    codigos = []
    for s in d.situacoes:
        if s["cst"] not in codigos:
            codigos.append(s["cst"])
    for data in (DATA_2027, DATA_2026):
        classes = d.classes_cbs(data)
        for cst in codigos + ["999"]:
            outra = next((c for c in classes if c["cst"] != cst and d.documentos(c, data)), None)
            item = g.item(outra, d.ncm_generico(data), data)
            item["cst"] = cst
            g.adicionar("nomenclatura", f"cst-{cst}-com-{outra['codigo']}-{data}",
                        _operacao(data, [item], g.local(), 55))
    data = DATA_2027
    classes = d.classes_cbs(data)
    vistos_cst = set()
    for classe in classes:
        docs = d.documentos(classe, data)
        situacao = d.situacao(classe["cst"], 2, data)
        if not docs:
            continue
        ncm = g.ncm_da_classe(classe, data)
        if ncm is None:
            continue
        # alíquotas nominais: REG-037 em 2027 sem elas e REG-036 em 2026 com elas, uma classificação por CST
        if situacao and situacao["grupo"] and classe["cst"] not in vistos_cst:
            vistos_cst.add(classe["cst"])
            g.adicionar("nomenclatura", f"sem-aliquotas-{classe['codigo']}-{data}",
                        _operacao(data, [g.item(classe, ncm, data, aliquotas=False)], g.local(), docs[0]))
            item = g.item(classe, g.ncm_da_classe(classe, DATA_2026) or ncm, DATA_2026)
            item["aliquotasNominais"] = dict(CENARIO_EM_2027)
            g.adicionar("nomenclatura", f"aliquotas-em-2026-{classe['codigo']}",
                        _operacao(DATA_2026, [item], g.local(), docs[0]))
        fora = d.ncm_fora_do_anexo(classe, data)
        if fora:
            g.adicionar("nomenclatura", f"ncm-fora-do-anexo-{classe['codigo']}",
                        _operacao(data, [g.item(classe, fora, data)], g.local(), docs[0]))
        if classe["desoneracao"]:
            item = g.item(classe, ncm, data)
            item.pop("tributacaoRegular")
            g.adicionar("nomenclatura", f"sem-tributacao-regular-{classe['codigo']}",
                        _operacao(data, [item], g.local(), docs[0]))
        if docs == [55]:
            g.adicionar("nomenclatura", f"sem-vinculo-nfce-{classe['codigo']}",
                        _operacao(data, [g.item(classe, ncm, data)], g.local(), 65))
    modelo = next(c for c in classes if c["codigo"] == "000001")
    # sem NCM, a 000001 (sem anexo) calcula: esses ficam no grupo cbs-ibs
    for grupo, nome, ncm, tp_doc in (("nomenclatura", "ncm-inexistente", "99999999", 55),
                                     ("nomenclatura", "ncm-incompleto", d.ncm_generico(data)[:6], 55),
                                     ("nomenclatura", "ncm-zero-sem-tpdoc", "00000000", None),
                                     ("cbs-ibs", "ncm-ausente-nfe", None, 55), ("cbs-ibs", "ncm-ausente-nfce", None, 65),
                                     ("cbs-ibs", "ncm-ausente-sem-tpdoc", None, None)):
        item = g.item(modelo, ncm, data)
        g.adicionar(grupo, nome, _operacao(data, [item], g.local(), tp_doc))
        g.adicionar(grupo, nome, _operacao(data, [item], g.local(), tp_doc), CAMINHO_REGIME_GERAL)
    for nome, alterar in (("cclasstrib-inexistente", {"cClassTrib": "999999"}), ("base-ausente", {"baseCalculo": None})):
        item = g.item(modelo, d.ncm_generico(data), data)
        for campo, valor in alterar.items():
            if valor is None:
                item.pop(campo)
            else:
                item[campo] = valor
        g.adicionar("nomenclatura", nome, _operacao(data, [item], g.local(), 55))


# --- o IS --------------------------------------------------------------------------------------------------

def _grupo_is(g: _Gerador, ncm: str, data: str, base: Decimal, variante: int) -> tuple[dict, Decimal | None]:
    """O grupo impostoSeletivo e a base do item: com o imposto informado, só a base do IS, ou base maior."""
    classe = g.d.classe_is_do_ncm(ncm, data)
    unidade = g.d.ad_rem(ncm, data) or "UN"
    grupo = {"cst": classe["cst"], "cClassTrib": classe["codigo"], "baseCalculo": base,
             "quantidade": g.rng.randint(1, 50), "unidade": unidade}
    if variante == 0:
        grupo["impostoInformado"] = Decimal("0")
        return grupo, base
    if variante == 1:
        return grupo, None
    return grupo, base + Decimal("10.00")


def _matriz_do_is(g: _Gerador) -> None:
    """Cada NCM com IS (ad valorem e ad rem): com o grupo e as alíquotas, sem o grupo (REG-013), sem alíquota
    (REG-035, um a cada cinco e todos os ad rem) e em 2026, quando o IS ainda não existe."""
    d = g.d
    data = DATA_2027
    modelo = next(c for c in d.classes_cbs(data) if c["codigo"] == "000001")
    for n, ncm in enumerate(d.ncms_com_is(data)):
        ad_rem = d.ad_rem(ncm, data)
        base = _base_calculo(g.rng)
        grupo, base_item = _grupo_is(g, ncm, data, base, n % 3)
        item = g.item(modelo, ncm, data)
        item["quantidade"] = grupo["quantidade"]
        item["unidade"] = grupo["unidade"]
        if base_item is None:
            item.pop("baseCalculo")
        else:
            item["baseCalculo"] = base_item
        item["impostoSeletivo"] = grupo
        aliquotas_is = {"adValorem": IS_AD_VALOREM}
        if ad_rem:
            aliquotas_is["adRem"] = IS_AD_REM
        com = copy.deepcopy(item)
        com["aliquotasNominais"]["impostoSeletivo"] = aliquotas_is
        tp_doc = (55, 65, None)[n % 3]
        g.adicionar("is", f"com-grupo-{ncm}", _operacao(data, [com], g.local(), tp_doc))
        if n % 4 == 0:
            g.adicionar("is", f"com-grupo-{ncm}", _operacao(data, [com], g.local(), tp_doc), CAMINHO_REGIME_GERAL)
        sem = copy.deepcopy(item)
        sem.pop("impostoSeletivo")
        sem["baseCalculo"] = base
        g.adicionar("is", f"sem-grupo-{ncm}", _operacao(data, [sem], g.local(), tp_doc))
        if n % 5 == 0 or ad_rem:
            g.adicionar("is", f"sem-aliquota-{ncm}", _operacao(data, [item], g.local(), tp_doc))
        if n % 5 == 0:
            antes = g.item(modelo, ncm, DATA_2026)
            g.adicionar("is", f"em-2026-{ncm}", _operacao(DATA_2026, [antes], g.local(), 55))
        if ad_rem:
            sem_unidade = copy.deepcopy(com)
            sem_unidade["impostoSeletivo"].pop("unidade")
            g.adicionar("is", f"ad-rem-sem-unidade-{ncm}", _operacao(data, [sem_unidade], g.local(), 55))
            sem_quantidade = copy.deepcopy(com)
            sem_quantidade["impostoSeletivo"].pop("quantidade")
            g.adicionar("is", f"ad-rem-sem-quantidade-{ncm}", _operacao(data, [sem_quantidade], g.local(), 55))
            quantidade_zero = copy.deepcopy(com)
            quantidade_zero["quantidade"] = 0
            g.adicionar("is", f"ad-rem-quantidade-zero-{ncm}", _operacao(data, [quantidade_zero], g.local(), 55))


# --- memória de cálculo ---------------------------------------------------------------------------------

def _matriz_da_memoria(g: _Gerador) -> None:
    """Quantidades que o Java escreve em notação científica ou com zeros à direita, em itens com e sem IS."""
    d = g.d
    data = DATA_2027
    classes = {c["codigo"]: c for c in d.classes_cbs(data)}
    ncm_ad_rem = next((n for n in d.ncms_com_is(data) if d.ad_rem(n, data)), None)
    ncm_ad_valorem = next((n for n in d.ncms_com_is(data) if not d.ad_rem(n, data)), None)
    com_reducao = next((c for c in classes.values() if c["reducao"] and not c["desoneracao"]
                        and d.documentos(c, data) and g.ncm_da_classe(c, data)), None)
    for quantidade in QUANTIDADES_DA_MEMORIA:
        q = Decimal(quantidade)
        for nome, classe in (("000001", classes["000001"]), ("reducao", com_reducao)):
            if classe is None:
                continue
            item = g.item(classe, g.ncm_da_classe(classe, data), data)
            item["quantidade"] = q
            g.adicionar("memoria", f"{nome}-quantidade-{quantidade}", _operacao(data, [item], g.local(), 55))
        for nome, ncm in (("is-ad-rem", ncm_ad_rem), ("is-ad-valorem", ncm_ad_valorem)):
            if ncm is None:
                continue
            item = g.item(classes["000001"], ncm, data)
            grupo, _ = _grupo_is(g, ncm, data, item["baseCalculo"], 0)
            grupo["quantidade"] = q
            item["quantidade"] = q
            item["unidade"] = grupo["unidade"]
            item["impostoSeletivo"] = grupo
            item["aliquotasNominais"]["impostoSeletivo"] = (
                {"adValorem": IS_AD_VALOREM, "adRem": IS_AD_REM} if nome == "is-ad-rem" else {"adValorem": IS_AD_VALOREM})
            g.adicionar("memoria", f"{nome}-quantidade-{quantidade}", _operacao(data, [item], g.local(), 55))
    item = g.item(classes["000001"], d.ncm_generico(data), data)
    item["quantidade"] = Decimal("1E+3")
    g.adicionar("memoria", "000001-quantidade-1E+3", _operacao(data, [item], g.local(), 55), CAMINHO_REGIME_GERAL)


# --- entrada ----------------------------------------------------------------------------------------------

def _entradas_ruins(g: _Gerador) -> None:
    """Uma violação por caso (com várias, a ordem das mensagens do Bean Validation não é garantida)."""
    d = g.d
    data = DATA_2027
    modelo = next(c for c in d.classes_cbs(data) if c["codigo"] == "000001")
    valido = _operacao(data, [g.item(modelo, d.ncm_generico(data), data)], (4314902, "RS"), 55)

    def com(caminho, valor):
        corpo = copy.deepcopy(valido)
        alvo = corpo
        for parte in caminho[:-1]:
            alvo = alvo[parte]
        if valor is _SEM:
            alvo.pop(caminho[-1])
        else:
            alvo[caminho[-1]] = valor
        return corpo

    grupo_is = {"cst": "000", "cClassTrib": "000001", "baseCalculo": Decimal("100.00"), "quantidade": 1, "unidade": "UN"}
    casos = [
        ("sem-id", ["id"], _SEM), ("sem-versao", ["versao"], _SEM), ("sem-municipio", ["municipio"], _SEM),
        ("municipio-acima", ["municipio"], 10000000), ("municipio-negativo", ["municipio"], -1),
        ("municipio-texto", ["municipio"], "abc"), ("uf-tres-letras", ["uf"], "RSS"), ("uf-inexistente", ["uf"], "XX"),
        ("municipio-de-outra-uf", ["uf"], "SP"), ("municipio-inexistente", ["municipio"], 9999999),
        ("sem-data", ["dhFatoGerador"], _SEM), ("data-sem-fuso", ["dhFatoGerador"], f"{data}T10:00:00"),
        ("data-so-dia", ["dhFatoGerador"], data),
        ("sem-itens", ["itens"], _SEM), ("itens-vazio", ["itens"], []),
        ("sem-numero", ["itens", 0, "numero"], _SEM), ("numero-zero", ["itens", 0, "numero"], 0),
        ("numero-acima", ["itens", 0, "numero"], 10000000), ("sem-cst", ["itens", 0, "cst"], _SEM),
        ("cst-dois-digitos", ["itens", 0, "cst"], "00"), ("cst-letras", ["itens", 0, "cst"], "0a0"),
        ("sem-cclasstrib", ["itens", 0, "cClassTrib"], _SEM), ("cclasstrib-cinco", ["itens", 0, "cClassTrib"], "00001"),
        ("cclasstrib-letras", ["itens", 0, "cClassTrib"], "00000a"),
        ("base-negativa", ["itens", 0, "baseCalculo"], Decimal("-1.00")),
        ("base-tres-casas", ["itens", 0, "baseCalculo"], Decimal("10.001")),
        ("base-catorze-digitos", ["itens", 0, "baseCalculo"], Decimal("12345678901234")),
        ("base-texto", ["itens", 0, "baseCalculo"], "dez"),
        ("quantidade-muitas-casas", ["itens", 0, "quantidade"], Decimal("1.123456789")),
        ("quantidade-cinco-casas", ["itens", 0, "quantidade"], Decimal("0.00001")),
        ("quantidade-doze-digitos", ["itens", 0, "quantidade"], Decimal("123456789012")),
        ("cbs-negativa", ["itens", 0, "aliquotasNominais", "cbs"], Decimal("-1")),
        ("cbs-acima-de-cem", ["itens", 0, "aliquotasNominais", "cbs"], Decimal("100.01")),
        ("cbs-cinco-casas", ["itens", 0, "aliquotasNominais", "cbs"], Decimal("11.30001")),
        ("ibs-estadual-acima-de-cem", ["itens", 0, "aliquotasNominais", "ibsEstadual"], Decimal("100.5")),
        ("regular-sem-cst", ["itens", 0, "tributacaoRegular"], {"cClassTrib": "000001"}),
        ("regular-cclasstrib-curta", ["itens", 0, "tributacaoRegular"], {"cst": "000", "cClassTrib": "1"}),
        ("is-sem-cst", ["itens", 0, "impostoSeletivo"], {k: v for k, v in grupo_is.items() if k != "cst"}),
        ("is-sem-base", ["itens", 0, "impostoSeletivo"], {k: v for k, v in grupo_is.items() if k != "baseCalculo"}),
        ("is-base-negativa", ["itens", 0, "impostoSeletivo"], dict(grupo_is, baseCalculo=Decimal("-5"))),
        ("is-quantidade-cinco-casas", ["itens", 0, "impostoSeletivo"], dict(grupo_is, quantidade=Decimal("0.00001"))),
        ("is-imposto-negativo", ["itens", 0, "impostoSeletivo"], dict(grupo_is, impostoInformado=Decimal("-1"))),
        ("is-ad-valorem-negativo", ["itens", 0, "aliquotasNominais", "impostoSeletivo"], {"adValorem": Decimal("-1")}),
    ]
    for nome, caminho, valor in casos:
        corpo = com(caminho, valor)
        g.adicionar("entrada", nome, corpo)
        g.adicionar("entrada", nome, corpo, CAMINHO_REGIME_GERAL)
    for nome, corpo in (("corpo-lista", []), ("corpo-texto", "operacao")):
        g.adicionar("entrada", nome, corpo)
        g.adicionar("entrada", nome, corpo, CAMINHO_REGIME_GERAL)
    # parecem erro de entrada mas calculam: a UF sai do município, e a quantidade do item só é criticada com IS
    for nome, caminho, valor in (("uf-ausente", ["uf"], _SEM), ("quantidade-zero", ["itens", 0, "quantidade"], 0),
                                 ("quantidade-negativa", ["itens", 0, "quantidade"], -1)):
        corpo = com(caminho, valor)
        g.adicionar("cbs-ibs", nome, corpo)
        g.adicionar("cbs-ibs", nome, corpo, CAMINHO_REGIME_GERAL)


_SEM = object()


# --- vários itens --------------------------------------------------------------------------------------------

def _itens_validos(g: _Gerador, data: str) -> list:
    """Itens que calculam: um por classificação da matriz, com NCM e vínculo com NF-e. Ficam fora as de
    tratamento com ajuste, que a oficial ainda não calcula (CAL-007): no /regime-geral elas seriam um segundo
    item inválido."""
    saida = []
    for classe in g.d.classes_cbs(data):
        if 55 not in g.d.documentos(classe, data) or classe["ajuste"]:
            continue
        ncm = g.ncm_da_classe(classe, data)
        if ncm:
            saida.append((classe, ncm))
    return saida


def _invalidos(g: _Gerador, data: str) -> list:
    d = g.d
    modelo = next(c for c in d.classes_cbs(data) if c["codigo"] == "000001")
    ncm = d.ncm_generico(data)
    saida = []
    for nome, alterar in (("ncm-inexistente", {"ncm": "99999999"}), ("cst-inexistente", {"cst": "999"}),
                          ("cclasstrib-inexistente", {"cClassTrib": "999999"}), ("cst-trocado", {"cst": "410"}),
                          ("sem-aliquotas", {"aliquotasNominais": None}), ("base-negativa", {"baseCalculo": Decimal("-1")})):
        item = g.item(modelo, ncm, data)
        for campo, valor in alterar.items():
            if valor is None:
                item.pop(campo, None)
            else:
                item[campo] = valor
        saida.append((nome, item))
    ncm_is = next(iter(d.ncms_com_is(data)), None)
    if ncm_is:
        saida.append(("is-sem-grupo", g.item(modelo, ncm_is, data)))
    return saida


def _numerar(itens: list) -> list:
    saida = []
    for n, item in enumerate(itens, 1):
        item = copy.deepcopy(item)
        item["numero"] = n
        saida.append(item)
    return saida


def _varios_itens(g: _Gerador) -> None:
    rng = g.rng
    data = DATA_2027
    validos = _itens_validos(g, data)
    invalidos = _invalidos(g, data)
    for k in range(12):
        tamanho = rng.randint(2, 8)
        escolhidos = [g.item(c, n, data) for c, n in rng.sample(validos, tamanho)]
        ruins = rng.sample(invalidos, 1 + k % 3)
        itens = escolhidos[:]
        for nome, ruim in ruins:
            itens.insert(rng.randint(0, len(itens)), ruim)
        nomes = "-".join(nome for nome, _ in ruins)
        g.adicionar("totais", f"mistura-{k}-{nomes}", _operacao(data, _numerar(itens), g.local(), 55))
        um = escolhidos[:]
        nome, ruim = ruins[0]
        um.insert(rng.randint(0, len(um)), ruim)
        g.adicionar("regime-geral", f"um-invalido-{k}-{nome}", _operacao(data, _numerar(um), g.local(), 55),
                    CAMINHO_REGIME_GERAL)
        g.adicionar("regime-geral", f"todos-validos-{k}", _operacao(data, _numerar(escolhidos), g.local(), 55),
                    CAMINHO_REGIME_GERAL)
    for k in range(3):
        itens = [ruim for _, ruim in rng.sample(invalidos, 2 + k)]
        g.adicionar("totais", f"nenhum-valido-{k}", _operacao(data, _numerar(itens), g.local(), 55))
    nao_implementado = next((c for c in g.d.classes_cbs(data) if c["cst"] == "620" and g.d.documentos(c, data)), None)
    if nao_implementado:
        itens = [g.item(nao_implementado, g.ncm_da_classe(nao_implementado, data), data),
                 invalidos[0][1]]
        g.adicionar("totais", "nenhum-valido-nao-implementado", _operacao(data, _numerar(itens), g.local(), 55))
    for data_lote in (DATA_2026,):
        itens = [g.item(c, n, data_lote) for c, n in rng.sample(_itens_validos(g, data_lote), 6)]
        g.adicionar("totais", f"todos-validos-{data_lote}", _operacao(data_lote, _numerar(itens), g.local(), 55))


# --- o lote do teste de arredondamento (GeradorLoteArredondamento.java, portado) ------------------------------

class _JavaRandom:
    """java.util.Random: o mesmo gerador linear congruente, para as notas saírem iguais às do Java."""

    _MULT, _MASCARA = 0x5DEECE66D, (1 << 48) - 1

    def __init__(self, semente: int):
        self.semente = (semente ^ self._MULT) & self._MASCARA

    def _proximo(self, bits: int) -> int:
        self.semente = (self.semente * self._MULT + 0xB) & self._MASCARA
        valor = self.semente >> (48 - bits)
        return valor - (1 << 32) if bits == 32 and valor >= 1 << 31 else valor

    def next_int(self, limite: int) -> int:
        if limite & -limite == limite:
            return (limite * self._proximo(31)) >> 31
        while True:
            bits = self._proximo(31)
            valor = bits % limite
            if bits - valor + (limite - 1) < 1 << 31:
                return valor

    def next_boolean(self) -> bool:
        return self._proximo(1) != 0

    def next_double(self) -> float:
        return ((self._proximo(26) << 27) + self._proximo(27)) * 2.0 ** -53


SEMENTE_DO_LOTE = 20261117
NOTAS_DO_LOTE = 40
_NCM_GERAL = "84713012"
# (cst, cClassTrib, ncm, redução, fator da base, regular)
_LOTE_NFE = [("000", "000001", _NCM_GERAL, "0", "1", None)] * 3 + [
    ("200", "200034", "03061200", "60", "1", None), ("200", "200034", "03061200", "60", "1", None),
    ("200", "200030", "39269030", "60", "1", None), ("200", "200038", "31010000", "60", "1", None),
    ("200", "200047", _NCM_GERAL, "40", "1", None), ("200", "200003", "10063011", "100", "1", None),
    ("510", "510001", _NCM_GERAL, "0", "1", None), ("515", "515001", "31010000", "60", "1", None),
    ("550", "550001", _NCM_GERAL, "0", "1", ("200", "200032")), ("550", "550001", _NCM_GERAL, "0", "1", ("000", "000001"))]
_LOTE_SEM_TPDOC = [("000", "000001", _NCM_GERAL, "0", "1", None), ("200", "200052", _NCM_GERAL, "30", "1", None),
                   ("200", "200047", _NCM_GERAL, "40", "1", None), ("200", "200046", _NCM_GERAL, "50", "1", None),
                   ("200", "200045", _NCM_GERAL, "60", "1", None), ("200", "200027", _NCM_GERAL, "70", "1", None),
                   ("200", "200026", _NCM_GERAL, "80", "1", None), ("222", "222001", _NCM_GERAL, "0", "0.5", None),
                   ("222", "222001", _NCM_GERAL, "0", "0.5", None), ("510", "510001", _NCM_GERAL, "0", "1", None)]
_LOTE_LOCAIS = [(3550308, "SP"), (4313409, "RS"), (1200013, "AC")]


def _plano(valor: Decimal) -> Decimal:
    """O número como o toPlainString do Java o escreve, guardado como Decimal (sem expoente)."""
    return Decimal(format(valor, "f"))


def _decimal_aleatorio(r: _JavaRandom, minimo: int, maximo: int, casas: int) -> Decimal:
    escala = 10 ** casas
    faixa = (maximo - minimo) * escala
    sorteado = minimo * escala + 1 + int(r.next_double() * (faixa - 1))
    return _plano(Decimal(sorteado).scaleb(-casas).normalize())


def _sortear_aliquotas(r: _JavaRandom) -> list:
    escolha = r.next_int(4)
    if escolha == 0:
        cbs = Decimal("8.8")
    elif escolha == 1:
        cbs = Decimal("8.8123")
    else:
        cbs = _decimal_aleatorio(r, 0, 12, r.next_int(5))
    if r.next_int(100) < 40:
        return [cbs, Decimal("0.05"), Decimal("0.05")]
    ibs_uf = _decimal_aleatorio(r, 0, 18, r.next_int(5))
    ibs_mun = Decimal("0") if r.next_int(10) == 0 else _decimal_aleatorio(r, 0, 5, r.next_int(5))
    return [cbs, ibs_uf, ibs_mun]


def _centavos_log(r: _JavaRandom) -> int:
    expoente = r.next_double() * 9.0
    centavos = math.floor(10.0 ** expoente + 0.5)
    return max(1, min(1_000_000_000, centavos))


def _centavos_para_empate(r: _JavaRandom, inicio: int, classe, trio) -> int:
    aliquota = trio[r.next_int(len(trio))]
    if aliquota == 0:
        aliquota = trio[0]
    with localcontext() as ctx:
        ctx.prec = 60
        fracao = aliquota.scaleb(-2) * (1 - Decimal(classe[3]).scaleb(-2)) * Decimal(classe[4])
        if fracao == 0:
            return inicio
        melhor, menor = inicio, None
        for centavos in range(inicio, min(1_000_000_000, inicio + 400) + 1):
            valor = Decimal(centavos) * fracao
            distancia = abs(valor - valor.to_integral_value(rounding=ROUND_FLOOR) - Decimal("0.5"))
            if menor is None or distancia < menor:
                menor, melhor = distancia, centavos
                if distancia == 0:
                    break
    return melhor


def _sortear_base(r: _JavaRandom, classe, trio) -> Decimal:
    tipo = r.next_int(100)
    if tipo < 15:
        return Decimal(1 + r.next_int(2000)).scaleb(-2)
    centavos = _centavos_log(r)
    if tipo < 75:
        return Decimal(centavos).scaleb(-2)
    return Decimal(_centavos_para_empate(r, centavos, classe, trio)).scaleb(-2)


def _nota_do_lote(indice: int):
    """(perfil, corpo) da nota de índice `indice`, ou None nas de compra governamental (fora do escopo)."""
    r = _JavaRandom(SEMENTE_DO_LOTE * 1_000_003 + indice)
    sorteio = r.next_int(100)
    if sorteio < 30:
        perfil, ano, com_tp_doc, classes, com_is = "2026-banco", 2026, True, _LOTE_NFE, False
    elif sorteio < 60:
        perfil, ano, com_tp_doc, classes, com_is = "2027-informada", 2027, True, _LOTE_NFE, False
    elif sorteio < 80:
        return None
    elif sorteio < 90:
        ano = 2026 if r.next_boolean() else 2027
        perfil, com_tp_doc, classes, com_is = f"sem-tpdoc-{ano}", False, _LOTE_SEM_TPDOC, False
    else:
        perfil, ano, com_tp_doc, classes, com_is = "imposto-seletivo-2027", 2027, True, _LOTE_NFE, True
    informadas = ano >= 2027
    local = _LOTE_LOCAIS[r.next_int(len(_LOTE_LOCAIS))]
    quantidade_itens = 1 + r.next_int(20)
    trio_unico = r.next_boolean()
    trio_nota = _sortear_aliquotas(r)
    itens = []
    for numero in range(1, quantidade_itens + 1):
        if not informadas:
            trio = [Decimal("0.9"), Decimal("0.1"), Decimal("0")]
        else:
            trio = trio_nota if trio_unico else _sortear_aliquotas(r)
        aliquotas = {"cbs": trio[0], "ibsEstadual": trio[1], "ibsMunicipal": trio[2]}
        if com_is and r.next_int(100) < 60:
            base = _sortear_base(r, classes[0], trio)
            quantidade = 1 + r.next_int(50)
            ad_valorem = _decimal_aleatorio(r, 1, 30, r.next_int(5))
            ad_rem = _decimal_aleatorio(r, 0, 25, r.next_int(5))
            itens.append({"numero": numero, "cst": "000", "cClassTrib": "000001", "ncm": "24021000",
                          "quantidade": quantidade, "unidade": "VN", "baseCalculo": base,
                          "impostoSeletivo": {"cst": "000", "cClassTrib": "000001", "baseCalculo": base,
                                              "quantidade": quantidade, "unidade": "VN", "impostoInformado": 0},
                          "aliquotasNominais": dict(aliquotas, impostoSeletivo={"adValorem": ad_valorem,
                                                                                "adRem": ad_rem})})
            continue
        classe = classes[r.next_int(len(classes))]
        base = _sortear_base(r, classe, trio)
        item = {"numero": numero, "cst": classe[0], "cClassTrib": classe[1], "baseCalculo": base, "ncm": classe[2]}
        if classe[5]:
            item["tributacaoRegular"] = {"cst": classe[5][0], "cClassTrib": classe[5][1]}
        if informadas:
            item["aliquotasNominais"] = aliquotas
        itens.append(item)
    corpo = {"id": f"lote-{indice}", "versao": "0.0.1", "dhFatoGerador": f"{ano}-03-15T10:00:00-03:00",
             "municipio": local[0], "uf": local[1]}
    if com_tp_doc:
        corpo["tpDoc"] = 55
    corpo["itens"] = itens
    return perfil, corpo


def _lote_de_arredondamento(g: _Gerador) -> None:
    """As primeiras notas do lote do Java que não são de compra governamental, no /regime-geral."""
    indice = postas = 0
    while postas < NOTAS_DO_LOTE:
        nota = _nota_do_lote(indice)
        if nota is not None:
            perfil, corpo = nota
            g.adicionar("regime-geral", f"lote-{indice}-{perfil}", corpo, CAMINHO_REGIME_GERAL)
            if postas % 4 == 0:
                g.adicionar("totais", f"lote-{indice}-{perfil}", corpo)
            postas += 1
        indice += 1


# --- os casos ------------------------------------------------------------------------------------------------

def _escritos() -> list:
    texto = (PASTA / "casos-escritos.json").read_text(encoding="utf-8")
    return [{**c, "origem": "escrito"} for c in receita_paridade.ler_json(texto)]


_CACHE: dict = {}


def _chave_do_cache(base_completa, semente):
    caminho = Path(base_completa) if base_completa else BASE_DA_SKILL
    escritos = PASTA / "casos-escritos.json"
    return caminho, (str(caminho.resolve()), caminho.stat().st_mtime_ns, escritos.stat().st_mtime_ns, semente)


def casos(base_completa: Path | None = None, semente: int = SEMENTE_PADRAO) -> list:
    """Os casos da bateria: escritos e gerados da base, sempre na mesma ordem."""
    caminho, chave = _chave_do_cache(base_completa, semente)
    if chave not in _CACHE:
        g = _Gerador(_Dados(caminho), semente)
        _entradas_ruins(g)
        _matriz_de_classificacoes(g)
        _erros_de_nomenclatura(g)
        _matriz_do_is(g)
        _matriz_da_memoria(g)
        _varios_itens(g)
        _lote_de_arredondamento(g)
        escritos = _escritos()
        ids = {c["id"] for c in escritos}
        repetidos = ids & {c["id"] for c in g.casos}
        if repetidos:
            raise ValueError(f"ids repetidos entre escritos e gerados: {sorted(repetidos)[:5]}")
        _CACHE[chave] = (escritos + g.casos, g.omitidos)
    return copy.deepcopy(_CACHE[chave][0])


def omitidos(base_completa: Path | None = None, semente: int = SEMENTE_PADRAO) -> list:
    """O que a base pede e a matriz não gerou, com o motivo: {"grupo", "chave", "motivo"}."""
    casos(base_completa, semente)
    return copy.deepcopy(_CACHE[_chave_do_cache(base_completa, semente)[1]][1])


def amostra(casos: list, n: int) -> list:
    """n casos, sempre os mesmos: todos os escritos e, dos gerados, alguns de cada grupo e depois sempre do grupo
    que tem menos casos na amostra até ali; dentro do grupo, uma chave de cada antes de repetir chave.
    Saem na ordem da lista original."""
    escritos = [c for c in casos if c.get("origem") == "escrito"]
    if len(escritos) > n:
        raise ValueError(f"A amostra de {n} casos não comporta os {len(escritos)} casos escritos.")
    escolhidos = {c["id"] for c in escritos}
    por_grupo = {g: 0 for g in GRUPOS}
    for c in escritos:
        por_grupo[c["grupo"]] = por_grupo.get(c["grupo"], 0) + 1
    filas = {g: [] for g in GRUPOS}
    sorteador = random.Random(SEMENTE_PADRAO)
    for grupo in GRUPOS:
        gerados = [c for c in casos if c.get("origem") != "escrito" and c["grupo"] == grupo]
        sorteador.shuffle(gerados)
        primeiros, resto, chaves = [], [], set()
        for c in gerados:
            chave = c.get("chave", c["id"])
            (resto if chave in chaves else primeiros).append(c)
            chaves.add(chave)
        filas[grupo] = primeiros + resto
    # primeiro uns poucos gerados de cada grupo, para todo grupo ter casos da matriz; depois o grupo mais magro
    for _ in range(MINIMO_DE_GERADOS_POR_GRUPO):
        for grupo in GRUPOS:
            if filas[grupo] and len(escolhidos) < n:
                escolhidos.add(filas[grupo].pop(0)["id"])
                por_grupo[grupo] += 1
    while len(escolhidos) < n and any(filas.values()):
        grupo = min((g for g in GRUPOS if filas[g]), key=lambda g: (por_grupo[g], GRUPOS.index(g)))
        escolhidos.add(filas[grupo].pop(0)["id"])
        por_grupo[grupo] += 1
    return [c for c in casos if c["id"] in escolhidos]
