"""Bateria de conferência da skill cbs-ibs-simples.

Gera da base da Receita a lista de requisições que a conferência faz à calculadora oficial
(escritos, matriz e entradas ruins), responde cada uma com a conta local e diz o que a lista cobre.

Exporta, para scripts/receita_paridade.py e para os testes:
    casos(base_completa=None, semente=20261122)  lista de casos, sempre na mesma ordem
    amostra(casos, n)                              n casos que cobrem tudo o que a lista cobre
    responder(requisicao)                          {"status", "corpo"} da conta local
    cobertura(respostas)                           códigos, estados, motivos e regimes vistos
    existentes(base=None)                          o que existe para ver (a base de comparação da cobertura)

Nada aqui usa a data de hoje nem sorteio sem semente: a mesma base e a mesma semente dão a mesma lista.
`casos` só lê as 25 tabelas do motor, na base reduzida da skill, que a base completa das migrações também tem
(a leitura é a mesma nas duas, e o teste confere); por isso `base_completa` pode ser omitida.
"""

from __future__ import annotations

import bisect
import copy
import datetime as dt
import importlib.util
import json
import random
import re
import sqlite3
import sys
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit

PASTA = Path(__file__).resolve().parent
RAIZ = PASTA.parent.parent
SKILL = RAIZ / "core" / "cd2-super" / "skills" / "cbs-ibs-simples"
SEMENTE_PADRAO = 20261122
BASE_URL = "http://127.0.0.1"  # a oficial gravada respondeu em http://127.0.0.1:8082/api
PREFIXO_API = "/api"
AGORA = dt.datetime(2026, 11, 22, 12, 0, 0, tzinfo=dt.timezone.utc)  # só o `timestamp` do erro, que a conferência ignora
CAMINHO_NAO_ASSISTIDO = "/calculadora/observabilidade/simples-nacional/nao-assistido"
CAMINHO_ATIVIDADES = "/calculadora/simples-nacional/atividades"
CAMINHO_ERROS = "/calculadora/observabilidade/simples-nacional/erros"
DATA_PADRAO = "2027-03-15"
LOTE_MAXIMO = 25  # itens por operação nos lotes, no máximo, e nunca acima do limite do documento
INICIO_DO_CALCULO = "2027-01-01"  # primeira vigência dos documentos que calculam (as datas anteriores só dão VSN-015)

# Domínios que só existem na validação do Java, sem tabela na base. Cada um espelha a linha citada, e o teste
# `DominiosDoJavaTest` confere que o motor aceita exatamente estes valores, para a lista não andar sozinha.
DOMINIOS_DO_JAVA = {
    "tpEnteGov": ("1", "2", "3", "4"),  # CalculoSimplesUnificadoService.java:415 (tpEnteGov < 1 || tpEnteGov > 4)
    "tribISSQN": ("1", "2", "3", "4"),  # EscolhaParAliquotasService.java:72 (DOMINIO_TRIBISSQN)
    "tpRetISSQN": ("1", "2", "3"),  # EscolhaParAliquotasService.java:73 (DOMINIO_TPRETISSQN)
    "tpBM": ("1", "2", "3", "4"),  # EscolhaParAliquotasService.java:74 (DOMINIO_TPBM)
}
# Siglas (normalizadas) em que a NBS nunca vale: ValidacaoNomenclaturaService.validarSemExigencia, TRIO_RIGIDO, linha
# `else if (nbsInformado) throw new NomenclaturaIncompativelTipoDfeException("NBS", ...)` (NF-e e NFC-e usam só NCM).
DOCUMENTOS_SEM_NBS = ("NFE", "NFCE")
# Classificações que o NomenclaturaService trata à parte: NF-e e NFC-e exigem NCM, NFS-e exige NBS (EXCEPTION_CLASSIFICACOES).
CLASSIFICACOES_DE_EXCECAO = ("200038",)


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


simples_base = _carregar("simples_base", SKILL / "scripts" / "simples_base.py")
simples_motor = _carregar("simples_motor", SKILL / "scripts" / "simples_motor.py")
receita_paridade = _carregar("receita_paridade", RAIZ / "scripts" / "receita_paridade.py")
calc = _carregar("calc_simples", SKILL / "scripts" / "calc.py")


# --- a conta local -------------------------------------------------------------------------------

_base_local = None


def _base():
    global _base_local
    if _base_local is None:
        _base_local = simples_base.Base()
    return _base_local


def _responder_com(base, requisicao: dict) -> dict:
    metodo = requisicao.get("metodo", "GET")
    partes = urlsplit(requisicao["caminho"])
    caminho = partes.path
    instancia = PREFIXO_API + caminho
    consulta = parse_qsl(partes.query, keep_blank_values=True)
    if metodo == "POST" and caminho == CAMINHO_NAO_ASSISTIDO:
        corpo = copy.deepcopy(requisicao.get("corpo"))
        status, saida = simples_motor.calcular(corpo, base, BASE_URL, instancia=instancia, agora=AGORA)
    elif metodo == "GET" and caminho == CAMINHO_ATIVIDADES:
        status, saida = base.atividades(*_filtros(consulta), instancia=instancia, base_url=BASE_URL, agora=AGORA)
    elif metodo == "GET" and caminho.startswith(CAMINHO_ATIVIDADES + "/"):
        codigo = unquote(caminho[len(CAMINHO_ATIVIDADES) + 1:])
        data, _, tp_doc, c_class_trib = _filtros(consulta)
        status, saida = base.atividade(codigo, data, tp_doc, c_class_trib, instancia=instancia,
                                       base_url=BASE_URL, agora=AGORA)
    elif metodo == "GET" and caminho == CAMINHO_ERROS:
        codigo = next((valor for nome, valor in consulta if nome == "codigo"), None)
        status, saida = simples_motor.consultar_erro(codigo, instancia=instancia, base_url=BASE_URL, agora=AGORA)
    elif metodo == "GET" and caminho == "/versao":
        saida = calc._executar_versao(base)["corpo"]
        status = 200
    else:
        status, saida = 404, None
    return {"status": status, "corpo": saida}


def _filtros(consulta):
    """(data, anexos, tp_doc, c_class_trib) dos parâmetros; anexos repetido vira lista."""
    unicos = {}
    anexos = None
    for nome, valor in consulta:
        if nome == "anexos":
            anexos = (anexos or []) + [valor]
        else:
            unicos.setdefault(nome, valor)
    return unicos.get("data"), anexos, unicos.get("tpDoc"), unicos.get("cClassTrib")


def responder(requisicao: dict) -> dict:
    """A resposta da conta local, com as mesmas convenções com que as oficiais foram gravadas."""
    return _responder_com(_base(), requisicao)


# --- o que a lista cobre -----------------------------------------------------------------------------

def cobertura(respostas: list) -> dict:
    """Códigos de erro, estados de item, motivos de não cálculo e regimes de apuração que as respostas mostram.

    Só as respostas de cálculo contam: o catálogo de erros e as consultas não mostram nada disso.
    """
    codigos, estados, motivos, regimes = set(), set(), set(), set()
    for resposta in respostas:
        corpo = resposta.get("corpo") if isinstance(resposta, dict) else None
        if not isinstance(corpo, dict):
            continue
        for erro in corpo.get("errors") or []:
            if isinstance(erro, dict) and isinstance(erro.get("code"), str):
                codigos.add(erro["code"])
        oper = corpo.get("oper")
        if isinstance(oper, dict) and isinstance(oper.get("regimeApuracao"), str):
            regimes.add(oper["regimeApuracao"])
        for objeto in corpo.get("objetos") or []:
            if not isinstance(objeto, dict):
                continue
            if isinstance(objeto.get("estadoItem"), str):
                estados.add(objeto["estadoItem"])
            motivo = objeto.get("motivoEstado")
            if isinstance(motivo, dict) and isinstance(motivo.get("codigo"), str):
                motivos.add(motivo["codigo"])
            for erro in objeto.get("erros") or []:
                if isinstance(erro, dict) and isinstance(erro.get("code"), str):
                    codigos.add(erro["code"])
    return {"codigos": sorted(codigos), "estados": sorted(estados), "motivos": sorted(motivos),
            "regimes": sorted(regimes)}


def existentes(base=None) -> dict:
    """O que a calculadora pode mostrar: os códigos e os motivos do motor, os estados do item e os regimes da base."""
    proprio = base is None
    base = _base() if proprio else base
    regimes = [linha[0] for linha in base._con.execute(
        "SELECT RASN_CD FROM REGIME_APURACAO_SIMPLES_NACIONAL ORDER BY RASN_ID")]
    return {"codigos": sorted(simples_motor.CODIGOS),
            "estados": sorted({simples_motor.CALCULADO, simples_motor.NAO_CALCULADO, simples_motor.NAO_IMPLEMENTADO,
                               simples_motor.INCONSISTENCIA_ENTRADA, simples_motor.FALHA_TECNICA_ESTADO}),
            "motivos": sorted(simples_motor._MOTIVOS_NAO_CALCULO),
            "regimes": sorted(regimes)}


# --- cobertura de pares ---------------------------------------------------------------------------------

def _pares(niveis: list, rng: random.Random, candidatos: int = 5, amostra_de_niveis: int = 10) -> list:
    """Linhas (listas de índices de nível, uma por fator) que juntas cobrem todo par de níveis de dois fatores.

    Guloso, no estilo AETG: parte de um par ainda não coberto e completa a linha com os níveis que
    cobrem mais pares novos. Determinístico para a mesma semente.
    """
    n = len(niveis)
    if n == 0:
        return []
    if n == 1:
        return [[i] for i in range(niveis[0])]
    pendentes = []
    for i in range(n):
        for j in range(i + 1, n):
            for a in range(niveis[i]):
                for b in range(niveis[j]):
                    pendentes.append((i, a, j, b))
    nao_cobertos = set(pendentes)
    ponteiro = 0
    linhas = []
    while nao_cobertos:
        while pendentes[ponteiro] not in nao_cobertos:
            ponteiro += 1
        i0, a0, j0, b0 = pendentes[ponteiro]
        melhor, melhor_ganho = None, -1
        for _ in range(candidatos):
            linha = [None] * n
            linha[i0], linha[j0] = a0, b0
            fixos = [i0, j0]
            resto = [f for f in range(n) if f not in (i0, j0)]
            rng.shuffle(resto)
            ganho = 1
            for f in resto:
                opcoes = list(range(niveis[f]))
                if len(opcoes) > amostra_de_niveis:
                    opcoes = rng.sample(opcoes, amostra_de_niveis)
                melhor_nivel, melhor_novos = opcoes[0], -1
                for nivel in opcoes:
                    novos = 0
                    for g in fixos:
                        chave = (g, linha[g], f, nivel) if g < f else (f, nivel, g, linha[g])
                        if chave in nao_cobertos:
                            novos += 1
                    if novos > melhor_novos:
                        melhor_nivel, melhor_novos = nivel, novos
                linha[f] = melhor_nivel
                fixos.append(f)
                ganho += max(melhor_novos, 0)
            if ganho > melhor_ganho:
                melhor, melhor_ganho = linha, ganho
        for i in range(n):
            for j in range(i + 1, n):
                nao_cobertos.discard((i, melhor[i], j, melhor[j]))
        linhas.append(melhor)
    return linhas


# --- a base ------------------------------------------------------------------------------------------------

def _dia_depois(texto: str, dias: int) -> str | None:
    try:
        return (dt.date.fromisoformat(texto[:10]) + dt.timedelta(days=dias)).isoformat()
    except ValueError:
        return None


class _Dados:
    """O que a matriz lê da base: datas, documentos, regimes, atividades, classificações e nomenclaturas."""

    def __init__(self, caminho):
        con = sqlite3.connect(f"{Path(caminho).resolve().as_uri()}?mode=ro", uri=True)
        try:
            self._ler(con)
        finally:
            con.close()

    def _ler(self, con):
        sql = lambda texto, *p: con.execute(texto, p).fetchall()  # noqa: E731
        self.datas = self._datas(con)
        self.docs = [{"tpDoc": str(linha[0]), "sigla": linha[1], "vocabulario": linha[2], "maximo": linha[3],
                      "casas": linha[4], "admite_is": linha[5] == 1, "tipo_dfe_id": linha[6]}
                     for linha in sql("SELECT d.TPDF_TIPO, d.TPDF_SIGLA, c.TDCS_VOCABULARIO_REGIME, c.TDCS_MAXIMO_ITENS, "
                                      "c.TDCS_CASAS_ALIQUOTA_CALCULO, c.TDCS_IN_ADMITE_IS, d.TPDF_ID "
                                      "FROM TIPO_DFE_CALCULO_SIMPLES_NACIONAL c JOIN TIPO_DFE d ON d.TPDF_ID = c.TDCS_TPDF_ID "
                                      "ORDER BY d.TPDF_TIPO")]
        self.doc_padrao = next(d for d in self.docs if d["vocabulario"] == "CRT")  # o primeiro que calcula com CRT
        calculam = {d["tpDoc"] for d in self.docs}
        self.docs_sem_calculo = [str(r[0]) for r in sql("SELECT TPDF_TIPO FROM TIPO_DFE ORDER BY TPDF_TIPO")
                                 if str(r[0]) not in calculam]
        self.atividades = [r[0] for r in sql("SELECT ATSN_CATIVSN FROM ATIVIDADE_SIMPLES_NACIONAL ORDER BY ATSN_ORDEM")]
        self.regimes_apuracao = [r[0] for r in sql("SELECT RASN_CD FROM REGIME_APURACAO_SIMPLES_NACIONAL ORDER BY RASN_ID")]
        enq = sql("SELECT ERSN_VOCABULARIO_REGIME, ERSN_CRT, ERSN_TPREGIMEIBSCBS, ERSN_OPSIMPNAC, ERSN_REGAPIBSCBSSN "
                  "FROM ENQUADRAMENTO_REGIME_SIMPLES_NACIONAL ORDER BY ERSN_ID")
        self.enquadramentos = enq
        self.campos_iss = {}
        for campo, valor in sql("SELECT SIPA_CAMPO, SIPA_VALOR FROM SITUACAO_ISS_PAR_ALIQUOTA_SIMPLES_NACIONAL "
                                "ORDER BY SIPA_ORDEM, SIPA_ID"):
            self.campos_iss.setdefault(campo, [])
            if str(valor) not in self.campos_iss[campo]:
                self.campos_iss[campo].append(str(valor))
        # valores da tabela, mais os que faltam até o maior e o seguinte ao maior (o vizinho inválido)
        self.dominios = {
            "crt": self._dominio(r[1] for r in enq if r[0] == "CRT"),
            "tpRegimeIBSCBS": self._dominio(r[2] for r in enq if r[0] == "CRT"),
            "opSimpNac": self._dominio(r[3] for r in enq if r[0] == "NFSE"),
            "regApIBSCBSSN": self._dominio(r[4] for r in enq if r[0] == "NFSE"),
            "regApTribSN": self._dominio([r[4] for r in enq if r[0] == "NFSE"]
                                         + [int(v) for v in self.campos_iss.get("regApTribSN", [])]),
        }
        self.optantes = sorted({str(r[3]) for r in enq if r[0] == "NFSE"})  # opSimpNac que a tabela enquadra
        self.padrao_do_regime = {}
        for vocabulario, crt, tp, op, reg in sql(
                "SELECT e.ERSN_VOCABULARIO_REGIME, e.ERSN_CRT, e.ERSN_TPREGIMEIBSCBS, e.ERSN_OPSIMPNAC, "
                "e.ERSN_REGAPIBSCBSSN FROM ENQUADRAMENTO_REGIME_SIMPLES_NACIONAL e JOIN REGIME_APURACAO_SIMPLES_NACIONAL r "
                "ON r.RASN_ID = e.ERSN_RASN_ID WHERE r.RASN_IN_CALCULA_TRIBUTOS = 1 AND r.RASN_IN_GERA_IBS = 1 "
                "ORDER BY e.ERSN_ID"):
            if vocabulario in self.padrao_do_regime:
                continue
            if vocabulario == "CRT":
                self.padrao_do_regime[vocabulario] = {"CRT": str(crt), **({"tpRegimeIBSCBS": str(tp)} if tp else {})}
            else:  # a tabela enquadra só optantes, e o optante precisa do regApTribSN
                self.padrao_do_regime[vocabulario] = {"opSimpNac": str(op), "regApIBSCBSSN": str(reg),
                                                      "regApTribSN": self.dominios["regApTribSN"][0]}
        self.tp_rbsn = sorted({r[0] for r in sql("SELECT CLTR_TPRBSN FROM CLASSIFICACAO_TRIBUTARIA")}
                              | {r[0] for r in sql("SELECT TRBS_CD FROM TIPO_RECEITA_BRUTA_SIMPLES_NACIONAL")})
        self.atividades_do_doc = {}
        for tipo, codigo, nao_tributavel in sql(
                "SELECT d.TPDF_TIPO, a.ATSN_CATIVSN, a.ATSN_IN_NAO_TRIBUTAVEL FROM TIPO_DFE_ATIVIDADE_SIMPLES_NACIONAL t "
                "JOIN TIPO_DFE d ON d.TPDF_ID = t.TDAS_TPDF_ID JOIN ATIVIDADE_SIMPLES_NACIONAL a ON a.ATSN_ID = t.TDAS_ATSN_ID "
                "ORDER BY a.ATSN_ORDEM"):
            self.atividades_do_doc.setdefault(str(tipo), []).append((codigo, nao_tributavel == 1))
        self.transicao = sql("SELECT TACG_FATOR, TACG_INICIO_VIGENCIA, TACG_FIM_VIGENCIA "
                             "FROM TRANSICAO_ALIQUOTA_COMPRA_GOV_SIMPLES_NACIONAL ORDER BY TACG_ID")
        self.classes = [
            {"id": r[0], "codigo": r[1], "tp_rbsn": r[2], "nomenclatura": r[3], "inicio": r[4], "fim": r[5],
             "ajuste": bool(r[6]), "cbs": bool(r[7])}
            for r in sql("SELECT c.CLTR_ID, c.CLTR_CD, c.CLTR_TPRBSN, c.CLTR_NOMENCLATURA, c.CLTR_INICIO_VIGENCIA, "
                         "c.CLTR_FIM_VIGENCIA, EXISTS (SELECT 1 FROM TRATAMENTO_CLASSIFICACAO tc "
                         "JOIN TRATAMENTO_TRIBUTARIO tt ON tt.TRTR_ID = tc.TRCL_TRTR_ID "
                         "WHERE tc.TRCL_CLTR_ID = c.CLTR_ID AND tt.TRTR_IN_POSSUI_AJUSTE = 1), "
                         "EXISTS (SELECT 1 FROM TRIBUTO_SITUACAO_TRIBUTARIA tst "
                         "WHERE tst.TRST_SITR_ID = c.CLTR_SITR_ID AND tst.TRST_TBTO_ID = 2) "
                         "FROM CLASSIFICACAO_TRIBUTARIA c ORDER BY c.CLTR_CD, c.CLTR_ID")]
        self.docs_da_classe = {}
        for cltr, tipo, inicio, fim in sql("SELECT t.TDCL_CLTR_ID, d.TPDF_TIPO, t.TDCL_INICIO_VIGENCIA, "
                                          "t.TDCL_FIM_VIGENCIA FROM TIPO_DFE_CLASSIFICACAO t "
                                          "JOIN TIPO_DFE d ON d.TPDF_ID = t.TDCL_TPDF_ID ORDER BY t.TDCL_ID"):
            self.docs_da_classe.setdefault(cltr, []).append((str(tipo), inicio, fim))
        self.ncm_aplicavel = {}  # classificação -> [(prefixo, início, fim)]
        for cltr, codigo, inicio, fim in sql("SELECT NCMA_CLTR_ID, NCMA_NCM_CD, NCMA_INICIO_VIGENCIA, NCMA_FIM_VIGENCIA "
                                            "FROM NCM_APLICAVEL ORDER BY NCMA_ID"):
            self.ncm_aplicavel.setdefault(cltr, []).append((codigo, inicio, fim))
        self.nbs_aplicavel = {}
        for cltr, codigo, inicio, fim in sql("SELECT NBSA_CLTR_ID, NBSA_NBS_CD, NBSA_INICIO_VIGENCIA, NBSA_FIM_VIGENCIA "
                                            "FROM NBS_APLICAVEL ORDER BY NBSA_ID"):
            self.nbs_aplicavel.setdefault(cltr, []).append((codigo, inicio, fim))
        self.excecoes_ncm = {}  # classificação -> [(prefixo da exceção, início, fim)]
        for cltr, codigo, inicio, fim in sql(
                "SELECT n.NCMA_CLTR_ID, e.ENCM_NCM_CD, e.ENCM_INICIO_VIGENCIA, e.ENCM_FIM_VIGENCIA "
                "FROM EXCECAO_NCM_APLICAVEL e JOIN NCM_APLICAVEL n ON n.NCMA_ID = e.ENCM_NCMA_ID ORDER BY e.ENCM_ID"):
            self.excecoes_ncm.setdefault(cltr, []).append((codigo, inicio, fim))
        self.datas_da_classe = {}  # classificação -> {"inicio": datas, "fim": datas} das linhas ligadas a ela
        for tabela, coluna_cltr, inicio, fim in (
                ("CLASSIFICACAO_TRIBUTARIA", "CLTR_ID", "CLTR_INICIO_VIGENCIA", "CLTR_FIM_VIGENCIA"),
                ("NCM_APLICAVEL", "NCMA_CLTR_ID", "NCMA_INICIO_VIGENCIA", "NCMA_FIM_VIGENCIA"),
                ("NBS_APLICAVEL", "NBSA_CLTR_ID", "NBSA_INICIO_VIGENCIA", "NBSA_FIM_VIGENCIA"),
                ("TIPO_DFE_CLASSIFICACAO", "TDCL_CLTR_ID", "TDCL_INICIO_VIGENCIA", "TDCL_FIM_VIGENCIA"),
                ("TRATAMENTO_CLASSIFICACAO", "TRCL_CLTR_ID", "TRCL_INICIO_VIGENCIA", "TRCL_FIM_VIGENCIA")):
            for cltr, a, b in sql(f"SELECT {coluna_cltr}, {inicio}, {fim} FROM {tabela}"):
                marcas = self.datas_da_classe.setdefault(cltr, {"inicio": set(), "fim": set()})
                if a:
                    marcas["inicio"].add(str(a)[:10])
                if b:
                    marcas["fim"].add(str(b)[:10])
        self.ncms = [r[0] for r in sql("SELECT NCM_CD FROM NCM WHERE length(NCM_CD) = 8 AND NCM_INICIO_VIGENCIA <= ? "
                                       "AND (NCM_FIM_VIGENCIA IS NULL OR NCM_FIM_VIGENCIA >= ?) ORDER BY NCM_CD",
                                       DATA_PADRAO, DATA_PADRAO)]
        self.nbss = [r[0] for r in sql("SELECT NBS_CD FROM NBS WHERE length(NBS_CD) = 9 AND NBS_INICIO_VIGENCIA <= ? "
                                       "AND (NBS_FIM_VIGENCIA IS NULL OR NBS_FIM_VIGENCIA >= ?) ORDER BY NBS_CD",
                                       DATA_PADRAO, DATA_PADRAO)]

    @staticmethod
    def _dominio(valores):
        """Os valores da tabela mais o primeiro que ela não tem (1 a máximo+1)."""
        usados = sorted({int(v) for v in valores if v is not None})
        if not usados:
            return []
        return [str(i) for i in range(1, usados[-1] + 2)]

    @staticmethod
    def _datas(con):
        """Datas distintas de vigência das tabelas lidas: início, fim, dia antes do início e dia depois do fim."""
        bruto = set()
        config = json.loads((PASTA / "config.json").read_text(encoding="utf-8"))
        # as tabelas da sugestão de classificação vão na base mas o motor não as lê: ficam fora das datas
        lidas = set(config["tabelas"]) - set(config.get("tabelas_da_sugestao", []))
        tabelas = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")
                   if r[0] in lidas]  # só as tabelas que a skill leva: a base completa tem muitas mais
        for tabela in tabelas:
            for _, coluna, *_ in con.execute(f"PRAGMA table_info({tabela})").fetchall():
                if coluna.endswith("INICIO_VIGENCIA") or coluna.endswith("FIM_VIGENCIA"):
                    for (valor,) in con.execute(f"SELECT DISTINCT {coluna} FROM {tabela} WHERE {coluna} IS NOT NULL"):
                        texto = str(valor)[:10]
                        if not texto.startswith("9999") and _dia_depois(texto, 0):
                            bruto.add(texto)
                            for passo in (-1, 1):
                                vizinho = _dia_depois(texto, passo)
                                if vizinho:
                                    bruto.add(vizinho)
        return sorted(bruto)

    def datas_de_calculo(self):
        return [d for d in self.datas if d >= INICIO_DO_CALCULO]

    def datas_anteriores(self):
        return [d for d in self.datas if d < INICIO_DO_CALCULO]

    def datas_de_classe(self, classe) -> list:
        """As datas de cálculo em que a linha da classificação é a efetiva e algo ligado a ela começa, acaba ou recomeça."""
        marcas = self.datas_da_classe.get(classe["id"], {"inicio": set(), "fim": set()})
        brutas = {INICIO_DO_CALCULO} | marcas["inicio"] | marcas["fim"]
        brutas |= {x for x in (_dia_depois(f, 1) for f in marcas["fim"]) if x}
        return sorted(d for d in brutas if d >= INICIO_DO_CALCULO and self.efetiva(classe, d))

    def efetiva(self, classe, data: str) -> bool:
        """A linha que a calculadora usa na data: a de início mais recente entre as vigentes de mesmo código."""
        return any(c["id"] == classe["id"] for c in self.classes_em(data))

    @staticmethod
    def vigentes(lista, data: str) -> list:
        return [codigo for codigo, inicio, fim in lista if inicio <= data and (fim is None or fim >= data)]

    def regime_padrao(self, doc: dict | None) -> dict:
        vocabulario = "NFSE" if doc is not None and doc["vocabulario"] == "NFSE" else "CRT"
        return dict(self.padrao_do_regime[vocabulario])

    def classes_em(self, data: str):
        """As classificações que a calculadora enxerga na data: uma por código (a de início mais recente), do grupo da CBS."""
        por_codigo = {}
        for c in self.classes:
            if c["cbs"] and c["inicio"] <= data and (c["fim"] is None or c["fim"] >= data):
                atual = por_codigo.get(c["codigo"])
                if atual is None or c["inicio"] > atual["inicio"]:
                    por_codigo[c["codigo"]] = c
        return list(por_codigo.values())

    def admite(self, classe, tp_doc: str, data: str) -> bool:
        return any(t == tp_doc and i <= data and (f is None or f >= data)
                   for t, i, f in self.docs_da_classe.get(classe["id"], []))

    def doc(self, tp_doc: str):
        return next((d for d in self.docs if d["tpDoc"] == tp_doc), None)


# --- montagem das requisições --------------------------------------------------------------------------------

_AUSENTE = object()  # campo que não vai no JSON
BASES = ["0.00", "0.01", "1.00", "33.33", "1000.00", "1234567.89", "99999999999.99", "9999999999999.99"]
SELETIVO = [None, "0.00", "12.34", "1000.00"]
PARES_OFICIAIS = {"anexoI": ("1.1612", "0.0129"), "anexoII": ("1.1184", "0.0121"),
                  "anexoIII": ("2.2154", "0.0257", "1.99", "0.0231"), "anexoIV": ("2.0119", "0.022", "1.85", "0.0202"),
                  "anexoV": ("3.2534", "0.0363", "3.1", "0.035")}
VARIANTES_DA_LINHA = ["padrao", "zeros", "sem-redistribuicao", "anexos-faltando", "par-incompleto", "casas-longas",
                      "aleatoria"]


def _valor(rng: random.Random, inteiros: int = 2, casas: int = 4) -> str:
    return f"{rng.randint(0, 10 ** inteiros - 1)}.{rng.randint(0, 10 ** casas - 1):0{casas}d}"


def _linha(variante: str, fator_r: bool, impedimento: bool, rng: random.Random) -> dict:
    """A linha de alíquotas efetivas (aliquotasEfetivas) de uma variante."""
    linha = {"fatorR": fator_r, "impedimentoSublimite": impedimento}
    for anexo in ("anexoI", "anexoII", "anexoIII", "anexoIV", "anexoV"):
        oficial = PARES_OFICIAIS[anexo]
        com_iss = anexo in ("anexoIII", "anexoIV", "anexoV")
        if variante == "zeros":
            cbs, ibs, sem_cbs, sem_ibs = "0", "0.0", "0.00", "0"
        elif variante == "aleatoria":
            cbs, ibs, sem_cbs, sem_ibs = _valor(rng), _valor(rng, 1), _valor(rng), _valor(rng, 1)
        elif variante == "casas-longas":
            cbs, ibs = "1." + "1234567890" * 3 + "1234", "0." + "0129" * 8 + "01"
            sem_cbs, sem_ibs = "1.99", "0.0231"
        else:
            cbs, ibs = oficial[0], oficial[1]
            sem_cbs, sem_ibs = (oficial[2], oficial[3]) if com_iss else (None, None)
        bloco = {"pCBSSN": cbs, "pIBSSN": ibs}
        if com_iss and variante != "sem-redistribuicao":
            bloco["semRedistribuicaoISS"] = {"pCBSSN": sem_cbs, "pIBSSN": sem_ibs}
        if variante == "par-incompleto" and anexo in ("anexoI", "anexoIII", "anexoV"):
            del bloco["pCBSSN" if anexo != "anexoIII" else "pIBSSN"]
        if variante == "anexos-faltando" and anexo in ("anexoII", "anexoIV"):
            continue
        linha[anexo] = bloco
    return linha


def _item(numero, atividade=None, classe=None, base="1000.00", seletivo=None, nomenclatura=None, iss=None) -> dict:
    item = {"numero": numero}
    if atividade is not None:
        item["cAtivSN"] = atividade
    if classe is not None:
        item["cClassTrib"] = classe
    if nomenclatura:
        item[nomenclatura[0]] = nomenclatura[1]
    if base is not None:
        item["baseCalculo"] = base
    if seletivo is not None:
        item["valorImpostoSeletivo"] = seletivo
    for campo, valor in (iss or {}).items():
        if valor is not None:
            item[campo] = valor
    return item


def _operacao(data, tp_doc, regime, ente, linha, itens, id_="op-1", versao="3.0.0") -> dict:
    corpo = {"id": id_, "versao": versao, "data": data, "tpDoc": tp_doc}
    corpo.update(regime)
    if ente is not None:
        corpo["tpEnteGov"] = ente
    if linha is not None:
        corpo["aliquotasEfetivas"] = linha
    corpo["itens"] = itens
    return corpo


def _requisicao(corpo) -> dict:
    return {"metodo": "POST", "caminho": CAMINHO_NAO_ASSISTIDO, "corpo": corpo}


class _SemClasse(Exception):
    """Não há classificação vigente do tipo pedido: a linha não vira caso e a omissão fica registrada."""


class _Gerador:
    def __init__(self, dados: _Dados, semente: int):
        self.d = dados
        self.rng = random.Random(semente)
        self.casos = []
        self.omitidos = []  # o que a base pede e a bateria não gerou: {"grupo", "chave", "motivo"}
        self._seq = {}

    def omitir(self, grupo: str, chave: str, motivo: str):
        self.omitidos.append({"grupo": grupo, "chave": chave, "motivo": motivo})

    def regime_padrao(self, doc):
        return self.d.regime_padrao(doc)

    def adicionar(self, grupo: str, requisicao: dict, rotulo: str = ""):
        n = self._seq.get(grupo, 0) + 1
        self._seq[grupo] = n
        identificador = f"{grupo}-{n:05d}" + (f"-{rotulo}" if rotulo else "")
        self.casos.append({"id": identificador, "grupo": grupo, "origem": "gerado", "requisicao": requisicao})

    # -- fatores ------------------------------------------------------------------------------------------

    def fatores_de_regime(self) -> list:
        """Os cinco campos de regime, cada um com ausente, os valores da base, os que faltam e o seguinte ao maior."""
        d = self.d
        return [[None] + d.dominios[c] for c in ("crt", "tpRegimeIBSCBS", "opSimpNac", "regApIBSCBSSN", "regApTribSN")]

    @staticmethod
    def regime_de(crt, tp_regime, op_simp_nac, reg_ap_ibscbssn, reg_ap_tribsn) -> dict:
        campos = (("CRT", crt), ("tpRegimeIBSCBS", tp_regime), ("opSimpNac", op_simp_nac),
                  ("regApIBSCBSSN", reg_ap_ibscbssn), ("regApTribSN", reg_ap_tribsn))
        return {nome: valor for nome, valor in campos if valor is not None}

    def regimes_validos(self) -> list:
        """Todas as linhas da tabela de enquadramento, nos dois vocabulários (a NFS-e optante leva regApTribSN)."""
        niveis = []
        for vocabulario, crt, tp, op, reg in self.d.enquadramentos:
            if vocabulario == "CRT":
                niveis.append({"CRT": str(crt), **({"tpRegimeIBSCBS": str(tp)} if tp else {})})
            else:
                regime = {"opSimpNac": str(op), "regApIBSCBSSN": str(reg)}
                if str(op) in self.d.optantes:
                    regime["regApTribSN"] = self.d.dominios["regApTribSN"][0]
                niveis.append(regime)
        return niveis

    def classe(self, nivel, data: str, tp_doc: str):
        """O cClassTrib de um nível: tpRBSN, ou 'nao-implementado', 'desconhecido', 'ausente', 'incompativel'."""
        if nivel == "ausente":
            return None
        if nivel == "desconhecido":
            return "999999"
        vigentes = self.d.classes_em(data)
        if nivel == "nao-implementado":
            candidatas = [c for c in vigentes if c["ajuste"]]
        elif nivel == "incompativel":
            candidatas = [c for c in vigentes if not self.d.admite(c, tp_doc, data)]
        else:
            candidatas = [c for c in vigentes if c["tp_rbsn"] == nivel and not c["ajuste"]]
            admitidas = [c for c in candidatas if self.d.admite(c, tp_doc, data)]
            if admitidas and self.rng.random() < 0.85:
                candidatas = admitidas
        if not candidatas:
            raise _SemClasse(f"nivel={nivel} data={data} tpDoc={tp_doc}")
        return self.rng.choice(candidatas)["codigo"]

    def nomenclatura(self, nivel, classe_codigo, data: str):
        """(campo, valor) ou None. A NCM aceita ou recusada é a da lista da classificação, quando ela tem lista."""
        if nivel == "nenhuma":
            return None
        rng, d = self.rng, self.d
        classe = next((c for c in d.classes_em(data) if c["codigo"] == classe_codigo), None)
        if nivel == "ncm-zero":
            return ("ncm", rng.choice(["00", "00000000"]))
        if nivel == "ncm-inexistente":
            return ("ncm", "99999999")
        if nivel == "nbs-inexistente":
            return ("nbs", "999999999")
        if nivel == "formato":
            return rng.choice([("ncm", "1234"), ("NCM", "abcdefgh"), ("nbs", "12345"), ("NBS", "1234567890")])
        if nivel == "os-dois":
            return None  # montado no item, com NCM e NBS juntos
        if nivel in ("ncm-aceito", "ncm-recusado", "nbs-aceito", "nbs-recusado"):
            return self.nomenclatura_da_lista(nivel, classe, data)
        raise ValueError(f"nível de nomenclatura desconhecido: {nivel}")

    def nomenclatura_da_lista(self, nivel, classe, data: str):
        """(campo, valor) aceito ou recusado pela lista da classificação na data, ou None se não há candidato."""
        d = self.d
        campo, qual = nivel.split("-")
        aceito = qual == "aceito"
        if classe is None:
            prefixos = excecoes = None
        elif campo == "ncm":
            prefixos = d.vigentes(d.ncm_aplicavel.get(classe["id"], []), data)
            excecoes = d.vigentes(d.excecoes_ncm.get(classe["id"], []), data)
        else:
            prefixos, excecoes = d.vigentes(d.nbs_aplicavel.get(classe["id"], []), data), []
        escolhido = self._escolher(d.ncms if campo == "ncm" else d.nbss, prefixos or [], aceito, excecoes or [])
        return None if escolhido is None else (campo, escolhido)

    def _ncm(self, prefixos, aceito: bool):
        return self._escolher(self.d.ncms, prefixos, aceito, [])

    def _nbs(self, prefixos, aceito: bool):
        return self._escolher(self.d.nbss, prefixos, aceito, [])

    def _escolher(self, codigos, prefixos, aceito, excecoes):
        """Um código da lista que a classificação aceita (ou que recusa), ou None quando não existe: sem lista, vale qualquer."""
        rng = self.rng
        if not prefixos:
            return rng.choice(codigos)
        if aceito:
            dentro = []
            for prefixo in prefixos:
                inicio = bisect.bisect_left(codigos, prefixo)
                while inicio < len(codigos) and codigos[inicio].startswith(prefixo):
                    if not any(codigos[inicio].startswith(e) for e in excecoes):
                        dentro.append(codigos[inicio])
                    inicio += 1
            return rng.choice(dentro) if dentro else None
        for _ in range(200):
            candidato = rng.choice(codigos)
            if not any(candidato.startswith(p) for p in prefixos):
                return candidato
        fora = [c for c in codigos if not any(c.startswith(p) for p in prefixos)]  # a lista cobre quase tudo: confere
        return rng.choice(fora) if fora else None

    def iss(self, tribissqn, tpret, tpbm):
        return {"tribISSQN": tribissqn, "tpRetISSQN": tpret, "tpBM": tpbm}


ESPECIAIS_DE_CLASSE = ["nao-implementado", "desconhecido", "ausente", "incompativel"]
NIVEIS_NOMENCLATURA = ["nenhuma", "nenhuma", "ncm-aceito", "ncm-recusado", "nbs-aceito", "nbs-recusado",
                       "ncm-inexistente", "nbs-inexistente", "ncm-zero", "formato", "os-dois"]


def _niveis_rbsn(dados: _Dados, especiais=ESPECIAIS_DE_CLASSE) -> list:
    """Os tpRBSN da base (classificações e tipos de receita bruta) e os casos especiais de cClassTrib."""
    return list(dados.tp_rbsn) + list(especiais)


def _niveis_iss(dados: _Dados):
    """tribISSQN, tpRetISSQN e tpBM: ausente, o domínio do Java, o que a tabela de situações cita e o seguinte ao maior."""
    saida = []
    for campo in ("tribISSQN", "tpRetISSQN", "tpBM"):
        valores = set(DOMINIOS_DO_JAVA[campo]) | set(dados.campos_iss.get(campo, []))
        saida.append([None] + sorted(valores) + [str(max(int(v) for v in valores) + 1)])
    return tuple(saida)


def _entes(dados=None) -> list:
    return [None] + list(DOMINIOS_DO_JAVA["tpEnteGov"])


# --- os casos escritos --------------------------------------------------------------------------------------------

def _escritos() -> list:
    texto = (PASTA / "casos-escritos.json").read_text(encoding="utf-8")
    casos = receita_paridade.ler_json(texto)
    return [{**c, "origem": "escrito"} for c in casos]


# --- a matriz ----------------------------------------------------------------------------------------------------------

def _matriz_de_operacoes(g: _Gerador) -> None:
    """Uma operação, um item: pares entre todos os fatores da operação e do item."""
    d, rng = g.d, g.rng
    datas = d.datas_de_calculo()
    docs = [x["tpDoc"] for x in d.docs] + d.docs_sem_calculo
    trib, ret, bm = _niveis_iss(d)
    ativs = list(d.atividades) + [None, "7", " 01 ", "99", "abc"]
    fatores = ([datas, docs] + g.fatores_de_regime() + [_entes(), [False, True], [True, False], VARIANTES_DA_LINHA,
               ativs, _niveis_rbsn(d), BASES, SELETIVO, NIVEIS_NOMENCLATURA, trib, ret, bm])
    for linha in _pares([len(f) for f in fatores], rng, candidatos=4, amostra_de_niveis=8):
        (data, tp_doc, crt, tp_regime, op, reg, trib_sn, ente, impedimento, fator_r, variante, ativ, rbsn, base,
         seletivo, nomen, t, r, b) = (f[i] for f, i in zip(fatores, linha))
        regime = g.regime_de(crt, tp_regime, op, reg, trib_sn)
        try:
            classe = g.classe(rbsn, data, tp_doc)
        except _SemClasse as erro:
            g.omitir("matriz", f"{data}-{tp_doc}", f"sem classificação ({erro})")
            continue
        if nomen == "os-dois":
            item = _item(1, ativ, classe, base, seletivo, ("ncm", g._ncm([], True)))
            item["nbs"] = g._nbs([], True)
        else:
            item = _item(1, ativ, classe, base, seletivo, g.nomenclatura(nomen, classe, data), g.iss(t, r, b))
        corpo = _operacao(data, tp_doc, regime, ente, _linha(variante, fator_r, impedimento, rng), [item])
        g.adicionar("matriz", _requisicao(corpo))


def _matriz_de_lotes(g: _Gerador) -> None:
    """Várias operações, muitos itens: o contexto em pares e, em cada um, os itens em pares."""
    d, rng = g.d, g.rng
    datas = d.datas_de_calculo()
    docs = [x for x in d.docs if x["maximo"] != 1]
    regimes = [r for r in g.regimes_validos() if "CRT" in r]
    trib, ret, bm = _niveis_iss(d)
    ativs = list(d.atividades) + [None, "99"]
    niveis_rbsn = _niveis_rbsn(d, ["nao-implementado", "desconhecido", "ausente"])
    nomenclaturas = ["nenhuma", "ncm-aceito", "ncm-recusado", "nbs-aceito", "nbs-recusado", "ncm-inexistente"]
    itens_fatores = [ativs, niveis_rbsn, BASES, SELETIVO, nomenclaturas, trib, ret, bm]
    linhas_itens = _pares([len(f) for f in itens_fatores], rng, candidatos=4, amostra_de_niveis=8)
    contexto = [datas, [x["tpDoc"] for x in docs], regimes, _entes(), [False, True], [True, False],
                ["padrao", "aleatoria", "sem-redistribuicao", "par-incompleto"]]
    for linha in _pares([len(f) for f in contexto], rng, candidatos=4, amostra_de_niveis=8):
        data, tp_doc, regime, ente, impedimento, fator_r, variante = (f[i] for f, i in zip(contexto, linha))
        doc = d.doc(tp_doc)
        limite = min(doc["maximo"] or LOTE_MAXIMO, LOTE_MAXIMO)
        aliquotas = _linha(variante, fator_r, impedimento, rng)
        for inicio in range(0, len(linhas_itens), limite):
            itens = []
            for selecao in linhas_itens[inicio:inicio + limite]:
                ativ, rbsn, base, seletivo, nomen, t, r, b = (f[i] for f, i in zip(itens_fatores, selecao))
                try:
                    classe = g.classe(rbsn, data, tp_doc)
                except _SemClasse as erro:
                    g.omitir("lote", f"{data}-{tp_doc}", f"item sem classificação ({erro})")
                    continue
                itens.append(_item(len(itens) + 1, ativ, classe, base, seletivo,
                                   g.nomenclatura(nomen, classe, data), g.iss(t, r, b)))
            if itens:
                g.adicionar("lote", _requisicao(_operacao(data, tp_doc, regime, ente, aliquotas, itens)))


def _matriz_de_nfse(g: _Gerador) -> None:
    """NFS-e tem um item por operação: o ISS (tribISSQN, tpRetISSQN, tpBM) e as atividades em pares com o regime."""
    d, rng = g.d, g.rng
    nfse = [x for x in d.docs if x["vocabulario"] == "NFSE"]
    if not nfse:
        return
    trib, ret, bm = _niveis_iss(d)
    regime_nfse = [[None] + d.dominios[c] for c in ("opSimpNac", "regApIBSCBSSN", "regApTribSN")]
    fatores = ([d.datas_de_calculo()] + regime_nfse + [_entes(), [False, True], [True, False],
               ["padrao", "aleatoria", "sem-redistribuicao"], list(d.atividades) + [None],
               _niveis_rbsn(d, ["ausente"]), BASES[:6], trib, ret, bm])
    for linha in _pares([len(f) for f in fatores], rng, candidatos=4, amostra_de_niveis=8):
        data, op, reg, trib_sn, ente, impedimento, fator_r, variante, ativ, rbsn, base, t, r, b = (
            f[i] for f, i in zip(fatores, linha))
        tp_doc = nfse[0]["tpDoc"]
        try:
            classe = g.classe(rbsn, data, tp_doc)
        except _SemClasse as erro:
            g.omitir("matriz-nfse", f"{data}-{tp_doc}", f"sem classificação ({erro})")
            continue
        item = _item(1, ativ, classe, base, None, None, g.iss(t, r, b))
        corpo = _operacao(data, tp_doc, g.regime_de(None, None, op, reg, trib_sn), ente,
                          _linha(variante, fator_r, impedimento, rng), [item])
        g.adicionar("matriz-nfse", _requisicao(corpo))


def _matriz_de_enquadramento(g: _Gerador) -> None:
    """Cada linha da tabela de enquadramento, em todos os documentos do vocabulário dela, com ente e sem."""
    d, rng = g.d, g.rng
    for regime in g.regimes_validos():
        vocabulario = "CRT" if "CRT" in regime else "NFSE"
        trib_opcoes = d.dominios["regApTribSN"] if vocabulario == "NFSE" and "regApTribSN" in regime else [None]
        for doc in (x for x in d.docs if x["vocabulario"] == vocabulario):
            for trib in trib_opcoes:
                for ente in (None, DOMINIOS_DO_JAVA["tpEnteGov"][-1]):
                    completo = dict(regime)
                    if trib is not None:
                        completo["regApTribSN"] = trib
                    try:
                        classe = g.classe(1, DATA_PADRAO, doc["tpDoc"])
                    except _SemClasse:
                        g.omitir("matriz-enquadramento", doc["tpDoc"], "sem classificação de tpRBSN 1")
                        continue
                    ativ = next((a for a, nao in d.atividades_do_doc.get(doc["tpDoc"], []) if not nao), None)
                    corpo = _operacao(DATA_PADRAO, doc["tpDoc"], completo, ente, _linha("padrao", True, False, rng),
                                      [_item(1, ativ, classe, "1000.00", "0.00" if doc["admite_is"] else None)])
                    g.adicionar("matriz-enquadramento", _requisicao(corpo))


def _matriz_de_nomenclatura(g: _Gerador) -> None:
    """Por classificação, em cada data de vigência e em cada documento em que ela vale, o item que testa a lista.

    O item é válido para o documento (atividade admitida, IS zerado onde o documento admite): só a nomenclatura
    pode recusá-lo. Com lista de NCM ou de NBS saem o aceito e o recusado; o que não tem candidato é omitido e
    registrado em `omitidos`. Sem lista, uma NCM e uma NBS quaisquer, e um item sem nomenclatura.
    """
    d = g.d
    for classe in d.classes:
        for data in d.datas_de_classe(classe):
            chave = f"{classe['codigo']}-{data}"
            if classe["ajuste"]:
                g.omitir("matriz-nomenclatura", chave, "VSN-027: tratamento sem implementação, o item para antes da nomenclatura")
                continue
            docs = [x for x in d.docs if d.admite(classe, x["tpDoc"], data)]
            if not docs:
                g.omitir("matriz-nomenclatura", chave, "nenhum documento que calcula admite a classificação")
                continue
            tem_ncm = bool(d.vigentes(d.ncm_aplicavel.get(classe["id"], []), data))
            tem_nbs = bool(d.vigentes(d.nbs_aplicavel.get(classe["id"], []), data))
            for posicao, doc in enumerate(docs):
                sigla = re.sub(r"[^A-Z0-9]", "", doc["sigla"].upper())
                nbs_vale = sigla not in DOCUMENTOS_SEM_NBS
                if tem_nbs and not nbs_vale:
                    g.omitir("matriz-nomenclatura", f"{chave}-{doc['tpDoc']}-nbs",
                             f"{doc['sigla']} só usa NCM: a NBS é recusada por incompatibilidade (VSN-045), não pela lista")
                atividades = [a for a, nao in d.atividades_do_doc.get(doc["tpDoc"], []) if not nao]
                if not atividades:
                    g.omitir("matriz-nomenclatura", f"{chave}-{doc['tpDoc']}", "nenhuma atividade tributável admitida no documento")
                    continue
                if sigla == "NFSE" and classe["codigo"] in CLASSIFICACOES_DE_EXCECAO:
                    g.omitir("matriz-nomenclatura", f"{chave}-{doc['tpDoc']}-ncm",
                             "classificação de exceção: na NFS-e vale só a NBS, e a NCM é recusada por incompatibilidade (VSN-045)")
                    pedidos = ["nbs-aceito", "nbs-recusado"] if tem_nbs else ["nbs-aceito"]  # sem lista, qualquer NBS passa
                elif tem_ncm or tem_nbs:
                    pedidos = (["ncm-aceito", "ncm-recusado"] if tem_ncm else []) + (
                        ["nbs-aceito", "nbs-recusado"] if tem_nbs and nbs_vale else [])
                elif posicao == 0:  # sem lista: o primeiro documento basta para ver o que a classificação pede
                    pedidos = ["ncm-aceito", "nbs-aceito"]
                else:
                    pedidos = []
                for nivel in pedidos + ["nenhuma"]:
                    nomenclatura = g.nomenclatura(nivel, classe["codigo"], data) if nivel != "nenhuma" else None
                    if nivel != "nenhuma" and nomenclatura is None:
                        g.omitir("matriz-nomenclatura", f"{chave}-{doc['tpDoc']}-{nivel}",
                                 "a base não tem candidato " + ("aceito" if nivel.endswith("aceito") else "recusado")
                                 + " para a lista")
                        continue
                    iss = ({"tribISSQN": DOMINIOS_DO_JAVA["tribISSQN"][0], "tpRetISSQN": DOMINIOS_DO_JAVA["tpRetISSQN"][0]}
                           if doc["vocabulario"] == "NFSE" else None)  # a atividade com ISS exige os dois
                    item = _item(1, atividades[0], classe["codigo"], "1000.00", "0.00" if doc["admite_is"] else None,
                                 nomenclatura, iss)
                    corpo = _operacao(data, doc["tpDoc"], g.regime_padrao(doc), None,
                                      _linha("padrao", True, False, g.rng), [item])
                    g.adicionar("matriz-nomenclatura", _requisicao(corpo), f"{chave}-{doc['tpDoc']}-{nivel}")


def _matriz_de_vigencia(g: _Gerador) -> None:
    """As datas anteriores ao cálculo, nos dois vocabulários, e a compra governamental em cada ano da transição."""
    d, rng = g.d, g.rng
    for data in d.datas_anteriores():
        for doc in (d.doc_padrao, next((x for x in d.docs if x["vocabulario"] == "NFSE"), None)):
            if doc is None:
                continue
            for ente in (None, DOMINIOS_DO_JAVA["tpEnteGov"][1]):
                corpo = _operacao(data, doc["tpDoc"], g.regime_padrao(doc), ente, _linha("padrao", True, False, rng),
                                  [_item(1, "01", "000001")])
                g.adicionar("matriz-vigencia", _requisicao(corpo))
    anos = {int(x[1][:4]) for x in d.transicao} | {int(x[2][:4]) for x in d.transicao if x[2]}
    anos = sorted(anos | {max(anos) + 1})  # e o ano seguinte ao último da tabela
    for ano in anos:
        for dia in ("-01-01", "-06-30", "-12-31"):
            for doc in d.docs:
                for ente in DOMINIOS_DO_JAVA["tpEnteGov"]:
                    try:
                        classe = g.classe(1, f"{ano}{dia}", doc["tpDoc"])
                    except _SemClasse:
                        g.omitir("matriz-compra-gov", f"{ano}{dia}-{doc['tpDoc']}", "sem classificação de tpRBSN 1")
                        continue
                    ativ = next((a for a, nao in d.atividades_do_doc.get(doc["tpDoc"], []) if not nao), "01")
                    corpo = _operacao(f"{ano}{dia}", doc["tpDoc"], g.regime_padrao(doc), ente,
                                      _linha("padrao", True, False, rng), [_item(1, ativ, classe, "1000.00")])
                    g.adicionar("matriz-compra-gov", _requisicao(corpo))


# --- entradas ruins ----------------------------------------------------------------------------------------------------

VALORES_RUINS = [None, "", " ", "abc", "0", "-1", "5", "99999999999", "1.5", "+1", "01", " 1", "1 ", "１", "1e2",
                 "\u00a0", "1\u00a0", "\t1", "٣", "1_0", "0x1", "-0", "2147483648", "-2147483649",
                 "9223372036854775808", "1.0", "1,0", "true", "null", "NULL", "ａ", "\u0000", "\n", "x" * 70,
                 1, 0, -1, 2 ** 31, 2 ** 40, 2 ** 70, Decimal("1.5"), Decimal("1.0"), Decimal("1E+3"), Decimal("-0.0"),
                 Decimal("1E+400"), Decimal("-1E+400"), Decimal("1E-400"), Decimal("4.9E-324"),
                 Decimal("0.1234567890123456789"), Decimal("123456789012345678901234567890.5"),
                 True, False, [], {}, [1], ["a"], {"a": 1}, [None], [[]], [{}]]
DATAS_RUINS = ["2027-02-29", "2028-02-29", "2027-13-01", "2027-00-10", "2027-1-1", "27-01-01", "+2027-03-15",
               "02027-03-15", "10000-01-01", "9999-12-31", "0000-01-01", "0001-01-01", "-0001-01-01", "-0000-01-01",
               "0000-02-29", "-0004-02-29", "-2027-03-15", "-2028-02-29", "-2033-01-01", "-9998-12-31", "-9999-01-01",
               "-12027-01-01", "-00001-03-15", "-999999999-03-15", "-1000000000-03-15", "+10000-03-15",
               "+999999999-12-31", "+1000000000-01-01", "+02027-03-15", "+00001-03-15", "2027-03-15T10:00:00",
               "2027-03-15 ", " 2027-03-15", "2027-03-15\n", "\u00a02027-03-15", "２０２７-03-15", "９999-12-31",
               "2027-03-15+01:00", "2027-03-15Z", "2027-03-15z", "15/03/2027", "20270315"]
VALORES_RUINS_DECIMAIS = [None, "", " ", "abc", "-1", "-0.00", "0.001", "1e3", "1,5", ".5", "5.", "+5.00", " 10.00",
                          "0000000000001.00", "1.0", "1", "00", "9999999999999.99", "9999999999999.999", "٣.٠٠", "1_0",
                          "007.50", "0.50", "5.000", Decimal("1E+400"), Decimal("0.1234567890123456789"),
                          "10.00 ", "12345678901234.00", "99999999999999999", "0x10", "NaN", "１.00", 10, Decimal("10.5"),
                          Decimal("1E+3"), True, [], {}]
VALORES_RUINS_ALIQUOTA = [None, "", "abc", "-1", "1000", "100", "1e2", "1,5", ".5", "5.", "1." + "1" * 35,
                          "00.5", "000.5", "0000.5", "999.9999", "-0", "+1", "1_0", "1.5e1", "0.", "0.0", "٣.٠",
                          "0." + "0" * 33 + "1", " 1.0", Decimal("1.5"), 7, True, [], {}]
CAMPOS_DA_OPERACAO = ["id", "versao", "data", "tpDoc", "CRT", "tpRegimeIBSCBS", "opSimpNac", "regApIBSCBSSN",
                      "regApTribSN", "tpEnteGov", "aliquotasEfetivas", "itens"]
CAMPOS_DO_ITEM_TEXTO = ["numero", "cAtivSN", "cClassTrib", "ncm", "NCM", "nbs", "NBS", "tribISSQN", "tpRetISSQN", "tpBM"]
CAMPOS_DO_ITEM_VALOR = ["baseCalculo", "valorImpostoSeletivo"]


def _com(corpo: dict, caminho: list, valor) -> dict:
    """Cópia do corpo com o campo do caminho trocado (ou tirado, se o valor for _AUSENTE), na mesma posição."""
    copia = copy.deepcopy(corpo)
    alvo = copia
    for passo in caminho[:-1]:
        alvo = alvo[passo]
    chave = caminho[-1]
    if valor is _AUSENTE:
        alvo.pop(chave, None)
    else:
        alvo[chave] = valor
    return copia


def _entradas_ruins(g: _Gerador) -> None:
    d, rng = g.d, g.rng
    nfe = _operacao(DATA_PADRAO, d.doc_padrao["tpDoc"], g.regime_padrao(d.doc_padrao), None,
                    _linha("padrao", True, False, rng), [_item(1, "01", "000001", "1000.00", "0.00")])
    nfse_doc = next((x for x in d.docs if x["vocabulario"] == "NFSE"), None)
    bases = [("nfe", nfe)]
    if nfse_doc:
        bases.append(("nfse", _operacao(DATA_PADRAO, nfse_doc["tpDoc"], g.regime_padrao(nfse_doc), None,
                                        _linha("padrao", True, False, rng), [_item(1, "01", "000001", "1000.00")])))
    for nome, base in bases:
        for campo in CAMPOS_DA_OPERACAO:
            for valor in [_AUSENTE] + VALORES_RUINS:
                g.adicionar("ruim-operacao", _requisicao(_com(base, [campo], valor)), f"{nome}-{campo}")
        for anexo in ("anexoI", "anexoII", "anexoIII", "anexoIV", "anexoV"):
            blocos = [("pCBSSN",), ("pIBSSN",)] + ([("semRedistribuicaoISS", "pCBSSN"), ("semRedistribuicaoISS", "pIBSSN")]
                                                    if anexo in ("anexoIII", "anexoIV", "anexoV") else [])
            for bloco in blocos:
                for valor in [_AUSENTE] + VALORES_RUINS_ALIQUOTA:
                    caminho = ["aliquotasEfetivas", anexo, *bloco]
                    g.adicionar("ruim-aliquota", _requisicao(_com(base, caminho, valor)), f"{nome}-{anexo}")
            for valor in (_AUSENTE, None, "x", [], {}, 1, {"pCBSSN": "1.0"}, {"semRedistribuicaoISS": None},
                          {"pCBSSN": "1", "pIBSSN": "1", "semRedistribuicaoISS": "x"}):
                g.adicionar("ruim-aliquota", _requisicao(_com(base, ["aliquotasEfetivas", anexo], valor)),
                            f"{nome}-{anexo}-bloco")
        for campo in ("fatorR", "impedimentoSublimite"):
            for valor in [_AUSENTE, None, "true", "false", "sim", 1, 0, "", [], {}, "True"]:
                g.adicionar("ruim-aliquota", _requisicao(_com(base, ["aliquotasEfetivas", campo], valor)),
                            f"{nome}-{campo}")
        for campo in CAMPOS_DO_ITEM_TEXTO:
            for valor in [_AUSENTE] + VALORES_RUINS:
                g.adicionar("ruim-item", _requisicao(_com(base, ["itens", 0, campo], valor)), f"{nome}-{campo}")
        for campo in CAMPOS_DO_ITEM_VALOR:
            for valor in [_AUSENTE] + VALORES_RUINS_DECIMAIS:
                g.adicionar("ruim-item", _requisicao(_com(base, ["itens", 0, campo], valor)), f"{nome}-{campo}")
        item = base["itens"][0]
        for itens in ([], [None], [1], ["x"], [[]], [{}], [item, item], [item, None, dict(item, numero=2)],
                      [dict(item, numero=1), dict(item, numero=1)], [item] * 3, {"numero": 1}, "x", 7, None):
            g.adicionar("ruim-itens", _requisicao(_com(base, ["itens"], itens)), nome)
        for chave in ("desconhecido", "tpDocumento", "ncm"):
            extra = copy.deepcopy(base)
            extra[chave] = "x"
            g.adicionar("ruim-operacao", _requisicao(extra), f"{nome}-campo-extra-{chave}")
        extra = copy.deepcopy(base)
        extra["itens"][0]["extra"] = {"x": 1}
        g.adicionar("ruim-item", _requisicao(extra), f"{nome}-campo-extra-no-item")
    for corpo in ("x", 1, [], [1], True, {}, {"itens": []}, {"id": "a"}, Decimal("1.5")):
        g.adicionar("ruim-corpo", _requisicao(corpo))
    # id e versão com textos especiais, data em formatos, datas impossíveis e anos fora do calendário comum
    for valor in ("a" * 300, "ação", "\t", " "):
        g.adicionar("ruim-operacao", _requisicao(_com(nfe, ["id"], valor)), "id")
        g.adicionar("ruim-operacao", _requisicao(_com(nfe, ["versao"], valor)), "versao")
    for valor in DATAS_RUINS:
        g.adicionar("ruim-operacao", _requisicao(_com(nfe, ["data"], valor)), "data")
        if nfse_doc:
            g.adicionar("ruim-operacao", _requisicao(_com(bases[1][1], ["data"], valor)), "data-nfse")


def _vizinhos_invalidos(valores) -> list:
    """Do lado da entrada ruim: zero, o seguinte ao maior valor do domínio, o seguinte a esse e um não número."""
    maior = max(int(v) for v in valores)
    return ["0", str(maior + 1), str(maior + 2), "-1", "x"]


def _entradas_ruins_de_dominio(g: _Gerador) -> None:
    """Os vizinhos inválidos de cada domínio lido da base, no documento e no regime que os usam."""
    d, rng = g.d, g.rng
    nfse = next((x for x in d.docs if x["vocabulario"] == "NFSE"), None)
    crt = d.doc_padrao
    linha = _linha("padrao", True, False, rng)

    def base(doc, **extra):
        corpo = _operacao(DATA_PADRAO, doc["tpDoc"], g.regime_padrao(doc), None, copy.deepcopy(linha),
                          [_item(1, "01", "000001", "1000.00", "0.00" if doc["admite_is"] else None)])
        return corpo

    for campo, doc in (("CRT", crt), ("tpRegimeIBSCBS", crt), ("opSimpNac", nfse), ("regApIBSCBSSN", nfse),
                       ("regApTribSN", nfse)):
        if doc is None:
            continue
        chave = {"CRT": "crt"}.get(campo, campo)
        for valor in _vizinhos_invalidos(d.dominios[chave]):
            g.adicionar("ruim-dominio", _requisicao(_com(base(doc), [campo], valor)), campo)
    for valor in _vizinhos_invalidos(DOMINIOS_DO_JAVA["tpEnteGov"]):
        for doc in (crt, nfse):
            if doc is not None:
                g.adicionar("ruim-dominio", _requisicao(_com(base(doc), ["tpEnteGov"], valor)), "tpEnteGov")
    tipos = sorted({int(x["tpDoc"]) for x in d.docs} | {int(x) for x in d.docs_sem_calculo})
    for valor in ("0", str(tipos[-1] + 1), str(tipos[-1] + 2), "-1", "x"):
        g.adicionar("ruim-dominio", _requisicao(_com(base(crt), ["tpDoc"], valor)), "tpDoc")
    codigos = sorted(int(a) for a in d.atividades)
    for valor in ("00", f"{codigos[-1] + 1:02d}", f"{codigos[-1] + 2:02d}", "x"):
        g.adicionar("ruim-dominio", _requisicao(_com(base(crt), ["itens", 0, "cAtivSN"], valor)), "cAtivSN")
    if nfse is not None:
        for campo in ("tribISSQN", "tpRetISSQN", "tpBM"):
            for valor in _vizinhos_invalidos(DOMINIOS_DO_JAVA[campo]):
                g.adicionar("ruim-dominio", _requisicao(_com(base(nfse), ["itens", 0, campo], valor)), campo)


# --- números, ordem dos itens e defeitos combinados ----------------------------------------------------------------

def _decimal_longo(rng: random.Random, inteiros: int, casas: int) -> str:
    parte = str(rng.randint(0, 10 ** inteiros - 1))
    return parte + ("." + "".join(rng.choice("0123456789") for _ in range(casas)) if casas else "")


def _matriz_numerica(g: _Gerador) -> None:
    """Contas com alíquotas e bases sorteadas: arredondamento, casas do documento, compra governamental, IS."""
    d, rng = g.d, g.rng
    datas = [x for x in d.datas_de_calculo() if x >= "2028-12-31"] + ["2027-03-15", "2027-01-01"]
    for doc in d.docs:
        vocab = doc["vocabulario"]
        operacoes = 24 if doc["maximo"] != 1 else 160
        itens_por = 1 if doc["maximo"] == 1 else 12
        ativs = [a for a in d.atividades if a != "90"]
        for _ in range(operacoes):
            data = rng.choice(datas)
            aliquotas = {"fatorR": rng.random() < 0.5, "impedimentoSublimite": rng.random() < 0.15}
            for anexo in ("anexoI", "anexoII", "anexoIII", "anexoIV", "anexoV"):
                bloco = {"pCBSSN": _decimal_longo(rng, rng.randint(1, 3), rng.randint(0, 34)),
                         "pIBSSN": _decimal_longo(rng, rng.randint(1, 3), rng.randint(0, 34))}
                if anexo in ("anexoIII", "anexoIV", "anexoV"):
                    bloco["semRedistribuicaoISS"] = {"pCBSSN": _decimal_longo(rng, 2, rng.randint(0, 6)),
                                                     "pIBSSN": _decimal_longo(rng, 1, rng.randint(0, 6))}
                aliquotas[anexo] = bloco
            itens = []
            for numero in range(1, itens_por + 1):
                try:
                    classe = g.classe(rng.choice([1, 1, 1] + list(d.tp_rbsn)), data, doc["tpDoc"])
                except _SemClasse as erro:
                    g.omitir("numerico", f"{data}-{doc['tpDoc']}", f"item sem classificação ({erro})")
                    continue
                seletivo = _decimal_longo(rng, rng.randint(1, 6), 2) if doc["admite_is"] and rng.random() < 0.4 else None
                iss = g.iss(rng.choice([None, "1"]), None, None) if vocab == "NFSE" else None
                itens.append(_item(len(itens) + 1, rng.choice(ativs), classe, _decimal_longo(rng, rng.randint(1, 11), 2),
                                   seletivo, None, iss))
            if not itens:
                continue
            regime = g.regime_padrao(doc)
            if vocab == "CRT":
                regime = rng.choice([r for r in g.regimes_validos() if "CRT" in r])
            ente = rng.choice(_entes())
            g.adicionar("numerico", _requisicao(_operacao(data, doc["tpDoc"], regime, ente, aliquotas, itens)),
                        doc["tpDoc"])


def _matriz_de_ordem(g: _Gerador) -> None:
    """A ordem, a repetição e a ausência do número do item: a resposta sai em ordem de `numero`."""
    d, rng = g.d, g.rng
    aliquotas = _linha("padrao", True, False, rng)
    for _ in range(40):
        quantidade = rng.randint(2, 9)
        numeros = [rng.choice([None, 0, -3, 1, 2, 3, 7, 7, 1000, 2 ** 31 - 1, "4", "x", 2.5, " 5 "])
                   for _ in range(quantidade)]
        itens = []
        for indice, numero in enumerate(numeros):
            item = _item(1, rng.choice(d.atividades + [None, "99"]),
                         g.classe(rng.choice(list(d.tp_rbsn) + ["ausente"]), DATA_PADRAO, d.doc_padrao["tpDoc"]), rng.choice(BASES))
            if numero is None:
                item.pop("numero")
            elif isinstance(numero, float):
                item["numero"] = Decimal(str(numero))
            else:
                item["numero"] = numero
            itens.append(item)
        g.adicionar("ordem", _requisicao(_operacao(DATA_PADRAO, d.doc_padrao["tpDoc"], g.regime_padrao(d.doc_padrao), None, aliquotas, itens)))


def _defeitos_combinados(g: _Gerador) -> None:
    """Dois defeitos na mesma requisição: vale o primeiro que a oficial encontra, na ordem dela."""
    d, rng = g.d, g.rng
    nfe = _operacao(DATA_PADRAO, d.doc_padrao["tpDoc"], g.regime_padrao(d.doc_padrao), None,
                    _linha("padrao", True, False, rng), [_item(1, "01", "000001", "1000.00", "0.00")])
    nfse_doc = next((x for x in d.docs if x["vocabulario"] == "NFSE"), None)
    bases = [nfe]
    if nfse_doc:
        bases.append(_operacao(DATA_PADRAO, nfse_doc["tpDoc"], g.regime_padrao(nfse_doc), None,
                               _linha("padrao", True, False, rng), [_item(1, "01", "000001", "1000.00")]))
    pool = []
    for campo in CAMPOS_DA_OPERACAO:
        pool += [([campo], v) for v in rng.sample(VALORES_RUINS, 6)] + [([campo], _AUSENTE)]
    for campo in CAMPOS_DO_ITEM_TEXTO:
        pool += [(["itens", 0, campo], v) for v in rng.sample(VALORES_RUINS, 5)] + [(["itens", 0, campo], _AUSENTE)]
    for campo in CAMPOS_DO_ITEM_VALOR:
        pool += [(["itens", 0, campo], v) for v in rng.sample(VALORES_RUINS_DECIMAIS, 5)]
    for anexo in ("anexoI", "anexoIII", "anexoV"):
        for sub in ("pCBSSN", "pIBSSN"):
            pool += [(["aliquotasEfetivas", anexo, sub], v) for v in rng.sample(VALORES_RUINS_ALIQUOTA, 3)]
    for base in bases:
        for _ in range(450):
            (c1, v1), (c2, v2) = rng.sample(pool, 2)
            try:
                corpo = _com(_com(base, c1, v1), c2, v2)
            except (KeyError, IndexError, TypeError, AttributeError):
                continue
            g.adicionar("ruim-combinado", _requisicao(corpo))


# --- consultas ------------------------------------------------------------------------------------------------------------

def _consultas(g: _Gerador) -> None:
    d, rng = g.d, g.rng
    datas = d.datas + ["2027-03-15", "2035-06-30", "9999-12-31"]
    docs = [None] + [x["tpDoc"] for x in d.docs][:5] + d.docs_sem_calculo[:2] + ["91", "99", "abc", "", "055", "+55"]
    classes = [None, "000001", "410001", "010001", "200019", "999999", "abc", "", "000002", "011001"]
    anexos = [None, "I", "I,V", "III", "I,II,III,IV,V", "X", "", ["I", "V"], ["III", "V"], "i", "I,,V", "IV, V"]
    linhas = _pares([len(datas), len(docs), len(classes), len(anexos)], rng, candidatos=4, amostra_de_niveis=8)
    for linha in linhas:
        data, tp_doc, classe, anexo = (f[i] for f, i in zip([datas, docs, classes, anexos], linha))
        g.adicionar("consulta-matriz", _get(CAMINHO_ATIVIDADES, data, tp_doc, classe, anexo))
    g.adicionar("consulta-matriz", _get(CAMINHO_ATIVIDADES, None, None, None, None), "sem-data")
    for data in DATAS_RUINS + ["2027-1-1", "2027-02-30", "01/01/2027", "2027-01-01+01:00", "2027-01-01Z", "abc",
                               "2027-01-01+1:00", "2027-01-01+19:00", "2027-01-01+01:00:30", "-2027-01-01+01:00"]:
        g.adicionar("consulta-matriz", _get(CAMINHO_ATIVIDADES, data, None, None, None), "data")
        g.adicionar("consulta-matriz", _get(CAMINHO_ATIVIDADES + "/01", data, None, None, None), "data-por-codigo")
    codigos = list(d.atividades) + ["1", "99", "abc", "00", "090", " 01"]
    for linha in _pares([len(codigos), len(datas), len(docs), len(classes)], rng, candidatos=4, amostra_de_niveis=8):
        codigo, data, tp_doc, classe = (f[i] for f, i in zip([codigos, datas, docs, classes], linha))
        g.adicionar("consulta-matriz", _get(f"{CAMINHO_ATIVIDADES}/{_quote(codigo)}", data, tp_doc,
                                            classe, None), "por-codigo")
    for codigo in sorted(simples_motor.CODIGOS) + ["VSN-998", "vsn-001", "VSN-008", "VSN-009", "", "abc"]:
        g.adicionar("consulta-erro", {"metodo": "GET", "caminho": f"{CAMINHO_ERROS}?codigo={_quote(codigo)}"})
    g.adicionar("consulta-erro", {"metodo": "GET", "caminho": CAMINHO_ERROS}, "sem-codigo")
    g.adicionar("consulta-versao", {"metodo": "GET", "caminho": "/versao"})


def _quote(texto: str) -> str:
    from urllib.parse import quote
    return quote(texto, safe="")


def _get(caminho: str, data, tp_doc, classe, anexo) -> dict:
    parametros = []
    if data is not None:
        parametros.append(("data", data))
    if anexo is not None:
        for valor in (anexo if isinstance(anexo, list) else [anexo]):
            parametros.append(("anexos", valor))
    if tp_doc is not None:
        parametros.append(("tpDoc", tp_doc))
    if classe is not None:
        parametros.append(("cClassTrib", classe))
    consulta = "&".join(f"{nome}={_quote(valor)}" for nome, valor in parametros)
    return {"metodo": "GET", "caminho": caminho + ("?" + consulta if consulta else "")}


# --- as listas ----------------------------------------------------------------------------------------------------------------

_CACHE: dict = {}


def casos(base_completa=None, semente: int = SEMENTE_PADRAO) -> list:
    """Os casos da bateria: escritos, matriz e entradas ruins, sempre na mesma ordem.

    `base_completa` é a base de onde a matriz sai (padrão: a base da skill, que tem as mesmas linhas
    das 25 tabelas lidas). A mesma base e a mesma semente dão a mesma lista.
    """
    caminho = Path(base_completa) if base_completa else SKILL / "references" / "base-receita.db"
    chave = (str(caminho.resolve()), caminho.stat().st_mtime_ns, semente)
    if chave not in _CACHE:
        g = _Gerador(_Dados(caminho), semente)
        _consultas(g)
        _matriz_de_vigencia(g)
        _matriz_de_nomenclatura(g)
        _matriz_de_enquadramento(g)
        _matriz_de_operacoes(g)
        _matriz_de_nfse(g)
        _matriz_de_lotes(g)
        _matriz_numerica(g)
        _matriz_de_ordem(g)
        _entradas_ruins(g)
        _entradas_ruins_de_dominio(g)
        _defeitos_combinados(g)
        _CACHE[chave] = (_escritos() + g.casos, g.omitidos)
    return copy.deepcopy(_CACHE[chave][0])


def omitidos(base_completa=None, semente: int = SEMENTE_PADRAO) -> list:
    """O que a base pede e a bateria não gerou, com o motivo: {"grupo", "chave", "motivo"}."""
    casos(base_completa, semente)
    caminho = Path(base_completa) if base_completa else SKILL / "references" / "base-receita.db"
    return copy.deepcopy(_CACHE[(str(caminho.resolve()), caminho.stat().st_mtime_ns, semente)][1])


_RESPOSTAS_LOCAIS: dict = {}


def _vistos(caso: dict) -> dict:
    chave = receita_paridade.escrever_json(caso["requisicao"], None)  # a resposta é da requisição, não do id
    if chave not in _RESPOSTAS_LOCAIS:
        _RESPOSTAS_LOCAIS[chave] = cobertura([responder(caso["requisicao"])])
    return _RESPOSTAS_LOCAIS[chave]


def _tamanho(caso: dict) -> int:
    return len(receita_paridade.escrever_json(caso["requisicao"], None))


def amostra(casos: list, n: int) -> list:
    """n casos, sempre os mesmos: todos os escritos, depois os que cobrem o que ainda falta, depois o resto sorteado.

    Cobrir é mostrar cada código, estado, motivo e regime que a lista toda mostra (pela resposta da conta
    local). Os casos saem na ordem da lista original.
    """
    escritos = [c for c in casos if c.get("origem") == "escrito"]
    if len(escritos) > n:
        raise ValueError(f"A amostra de {n} casos não comporta os {len(escritos)} casos escritos.")
    escolhidos = {c["id"] for c in escritos}
    por_id = {c["id"]: c for c in casos}
    todos = {"codigos": set(), "estados": set(), "motivos": set(), "regimes": set()}
    vistos_na_amostra = {k: set() for k in todos}
    for caso in casos:
        for chave, valores in _vistos(caso).items():
            todos[chave].update(valores)
            if caso["id"] in escolhidos:
                vistos_na_amostra[chave].update(valores)
    candidatos = [c for c in casos if c["id"] not in escolhidos]
    faltam = lambda: any(todos[k] - vistos_na_amostra[k] for k in todos)  # noqa: E731
    while faltam() and len(escolhidos) < n:
        melhor, melhor_nota = None, (0, 0)
        for caso in candidatos:
            if caso["id"] in escolhidos:
                continue
            vistos = _vistos(caso)
            novos = sum(len(set(vistos[k]) - vistos_na_amostra[k]) for k in todos)
            nota = (novos, -_tamanho(caso))
            if novos and nota > melhor_nota:
                melhor, melhor_nota = caso, nota
        if melhor is None:
            break
        escolhidos.add(melhor["id"])
        for chave, valores in _vistos(melhor).items():
            vistos_na_amostra[chave].update(valores)
    resto = [c for c in candidatos if c["id"] not in escolhidos and _tamanho(c) <= 4000]
    sorteador = random.Random(SEMENTE_PADRAO)
    sorteador.shuffle(resto)
    for caso in resto[:max(n - len(escolhidos), 0)]:
        escolhidos.add(caso["id"])
    return [por_id[c["id"]] for c in casos if c["id"] in escolhidos]
