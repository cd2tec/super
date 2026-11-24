"""Testes do motor da skill cbs-ibs-regime-regular contra as respostas oficiais gravadas.

A paridade vai grupo a grupo: `paridade_do_grupo(grupo)` roda `bateria.responder` (que chama
`rr_calc.executar`) sobre os casos do grupo no esperadas.json e devolve os ids que divergem. Cada task do
plano liga os seus grupos em `GRUPOS_LIGADOS`.

Projeção. Até a Task 9, a comparação usou uma projeção das respostas (`PROJECAO = True`): o status HTTP,
o corpo inteiro das respostas de erro e, nas respostas 200, tudo menos o `total` da operação e as
`memoriaCalculo`. Desde a Task 10 (`PROJECAO = False`) a comparação é a do robô, inteira: memória de
cálculo e totais incluídos. `projetar` fica para conferir uma divergência por partes.

Parte de erro. Um grupo em `GRUPOS_SO_ERROS` é cobrado só no que não é sucesso (`divergencias_de_erro`):
as respostas de erro e os itens cujo `estadoItem` esperado não é CALCULADO nem CALCULO_SIMULADO têm de
sair iguais (dentro da projeção); os de sucesso têm de chegar ao cálculo sem erro de processamento (o
`rr_motor._calcular_item` levantava `CalculoNaoPortado`). Na Task 7 o grupo `nomenclatura` ficou
assim; desde a Task 8 nenhum grupo está nessa lista, e todo grupo ligado é cobrado inteiro.

As partes comparadas usam `receita_paridade.comparar` (Decimal pela forma escrita) com a lista `ignorar`
do config.json, como a conferência com a calculadora oficial.
"""
import contextlib
import copy
import importlib.util
import io
import json
import sys
import unittest
import urllib.error
from unittest import mock
from datetime import date
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "core" / "cd2-super" / "skills" / "cbs-ibs-regime-regular"
PASTA = ROOT / "conferencia" / "cbs-ibs-regime-regular"

# Desde a Task 10 a comparação é inteira, sem projeção.
PROJECAO = False
GRUPOS_LIGADOS = ["entrada", "nomenclatura", "cbs-ibs", "reducao", "diferimento", "tributacao-regular", "is",
                  "memoria", "totais", "regime-geral"]
# Grupos ligados cobrados só na parte de erro (ver a docstring).
GRUPOS_SO_ERROS = set()
SUCESSO = ("CALCULADO", "CALCULO_SIMULADO")


def load_module(name, path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


receita_paridade = load_module("receita_paridade", ROOT / "scripts" / "receita_paridade.py")
bateria = load_module("bateria_rr", PASTA / "bateria.py")
rr_erros = load_module("rr_erros", SKILL / "scripts" / "rr_erros.py")
rr_entrada = load_module("rr_entrada", SKILL / "scripts" / "rr_entrada.py")
# O calc.py faz `import rr_motor`: carregado aqui com o mesmo nome, o módulo é o mesmo objeto.
rr_motor = load_module("rr_motor", SKILL / "scripts" / "rr_motor.py")
rr_base = load_module("rr_base", SKILL / "scripts" / "rr_base.py")
rr_calc = load_module("rr_calc", SKILL / "scripts" / "calc.py")

ESPERADAS = receita_paridade.ler_json((PASTA / "esperadas.json").read_text(encoding="utf-8"))
CONFIG = json.loads((PASTA / "config.json").read_text(encoding="utf-8"))
IGNORAR = CONFIG.get("ignorar", [])


def _sem_chave(no, chave):
    if isinstance(no, dict):
        return {k: _sem_chave(v, chave) for k, v in no.items() if k != chave}
    if isinstance(no, list):
        return [_sem_chave(v, chave) for v in no]
    return no


def projetar(resposta: dict) -> dict:
    """A parte da resposta que a paridade compara enquanto PROJECAO vale (ver a docstring do módulo)."""
    corpo = resposta.get("corpo")
    if resposta.get("status") == 200 and isinstance(corpo, dict):
        corpo = _sem_chave({k: v for k, v in corpo.items() if k != "total"}, "memoriaCalculo")
    return {"status": resposta.get("status"), "corpo": corpo}


def diferencas_do_caso(caso: dict, projecao: bool = PROJECAO) -> list:
    """As diferenças entre a resposta oficial gravada e a local (vazia se iguais)."""
    oficial = caso["resposta"]
    try:
        local = bateria.responder(caso["requisicao"])
    except Exception as erro:  # o motor não pode derrubar a conferência do grupo inteiro
        return [f"local falhou: {type(erro).__name__}: {erro}"]
    if projecao:
        oficial, local = projetar(oficial), projetar(local)
    return receita_paridade.comparar(oficial, local, IGNORAR)


def paridade_do_grupo(grupo: str, projecao: bool = PROJECAO) -> list:
    """Os ids dos casos do grupo, no esperadas.json, cuja resposta local diverge da oficial."""
    return [c["id"] for c in ESPERADAS["casos"] if c["grupo"] == grupo and diferencas_do_caso(c, projecao)]


CALCULO_PENDENTE = "<cálculo pendente>"


def _calculo_pendente(*_args, **_kwargs):
    return CALCULO_PENDENTE


def divergencias_de_erro(caso: dict) -> list:
    """A parte de erro do caso (ver a docstring do módulo): vazia se a resposta local confere.

    Com `_calcular_item` trocado por um marcador, os erros de processamento saem como sairiam com o
    cálculo pronto; um caso de sucesso do /regime-geral é rodado sem a troca e tem de parar no cálculo.
    """
    oficial = caso["resposta"]
    requisicao = caso["requisicao"]
    observabilidade = "observabilidade" in requisicao["caminho"]
    if oficial["status"] == 200 and not observabilidade:
        try:
            local = bateria.responder(requisicao)
        except rr_motor.CalculoNaoPortado:
            return []
        except Exception as erro:
            return [f"local falhou: {type(erro).__name__}: {erro}"]
        return [f"esperava chegar ao cálculo, veio status {local.get('status')}"]
    try:
        with mock.patch.object(rr_motor, "_calcular_item", _calculo_pendente):
            local = bateria.responder(requisicao)
    except Exception as erro:
        return [f"local falhou: {type(erro).__name__}: {erro}"]
    if oficial["status"] != 200:
        return receita_paridade.comparar(projetar(oficial), projetar(local), IGNORAR)
    if local.get("status") != 200:
        return [f"status: 200 != {local.get('status')}"]
    esperados, locais = oficial["corpo"]["objetos"], local["corpo"]["objetos"]
    if len(esperados) != len(locais):
        return [f"objetos: {len(esperados)} != {len(locais)}"]
    saida = []
    for esperado, obtido in zip(esperados, locais):
        if esperado.get("estadoItem") in SUCESSO:
            alvo = {"nObj": esperado.get("nObj"), "estadoItem": esperado["estadoItem"], "tribCalc": CALCULO_PENDENTE}
            if obtido != alvo:
                saida.append(f"nObj {esperado.get('nObj')}: esperava chegar ao cálculo, veio {obtido}")
            continue
        saida += receita_paridade.comparar({"status": 200, "corpo": _sem_chave(esperado, "memoriaCalculo")},
                                           {"status": 200, "corpo": obtido}, IGNORAR)
    return saida


def erros_do_grupo(grupo: str) -> list:
    """Os ids dos casos do grupo cuja parte de erro diverge."""
    return [c["id"] for c in ESPERADAS["casos"] if c["grupo"] == grupo and divergencias_de_erro(c)]


class ParidadeTest(unittest.TestCase):
    def _conferir(self, grupo):
        self.assertIn(grupo, GRUPOS_LIGADOS)
        casos = [c for c in ESPERADAS["casos"] if c["grupo"] == grupo]
        self.assertTrue(casos, f"o esperadas.json não tem casos do grupo {grupo}")
        divergentes = paridade_do_grupo(grupo)
        detalhes = {i: diferencas_do_caso(next(c for c in casos if c["id"] == i))[:3] for i in divergentes[:10]}
        self.assertEqual(divergentes, [], json.dumps(detalhes, ensure_ascii=False, indent=1))

    def _conferir_erros(self, grupo):
        self.assertIn(grupo, GRUPOS_LIGADOS)
        casos = [c for c in ESPERADAS["casos"] if c["grupo"] == grupo]
        self.assertTrue(casos, f"o esperadas.json não tem casos do grupo {grupo}")
        divergentes = erros_do_grupo(grupo)
        detalhes = {i: divergencias_de_erro(next(c for c in casos if c["id"] == i))[:3] for i in divergentes[:10]}
        self.assertEqual(divergentes, [], json.dumps(detalhes, ensure_ascii=False, indent=1))

    def _conferir_grupo(self, grupo):
        if grupo in GRUPOS_SO_ERROS:
            self._conferir_erros(grupo)
        else:
            self._conferir(grupo)

    def test_grupo_entrada(self):
        self._conferir_grupo("entrada")

    def test_grupo_nomenclatura(self):
        self._conferir_grupo("nomenclatura")

    def test_grupo_cbs_ibs(self):
        self._conferir_grupo("cbs-ibs")

    def test_grupo_reducao(self):
        self._conferir_grupo("reducao")

    def test_grupo_diferimento(self):
        self._conferir_grupo("diferimento")

    def test_grupo_tributacao_regular(self):
        self._conferir_grupo("tributacao-regular")

    def test_grupo_is(self):
        self._conferir_grupo("is")

    def test_grupo_memoria(self):
        self._conferir_grupo("memoria")

    def test_grupo_totais(self):
        self._conferir_grupo("totais")

    def test_grupo_regime_geral(self):
        self._conferir_grupo("regime-geral")

    def test_todos_os_grupos_ligados(self):
        self.assertEqual(sorted({c["grupo"] for c in ESPERADAS["casos"]}), sorted(GRUPOS_LIGADOS))


OBSERVABILIDADE = "/calculadora/observabilidade/regime-geral"
REGIME_GERAL = "/calculadora/regime-geral"


def _operacao(itens, dh="2026-10-20T10:00:00-03:00"):
    operacao = {"id": "t10", "versao": "0.0.1", "dhFatoGerador": dh, "municipio": 3550308, "uf": "SP",
                "itens": itens}
    if dh < "2027":
        operacao["tpDoc"] = 55
    return operacao


NOMINAIS = {"cbs": Decimal("8.8"), "ibsEstadual": Decimal("8.85"), "ibsMunicipal": Decimal("8.85")}


ITEM_OK = {"cst": "000", "cClassTrib": "000001", "baseCalculo": Decimal("25.0"), "ncm": "84713012"}
ITEM_INVALIDO = {"cst": "000", "cClassTrib": "999999", "baseCalculo": Decimal("40"), "ncm": "84713012"}


class TotaisTest(unittest.TestCase):
    """ObservabilidadeService: o total soma só os itens CALCULADO e CALCULO_SIMULADO, e some sem nenhum."""

    def _observabilidade(self, itens):
        resposta = bateria.responder({"metodo": "POST", "caminho": OBSERVABILIDADE, "corpo": _operacao(itens)})
        self.assertEqual(resposta["status"], 200)
        return resposta["corpo"]

    def test_totais_so_dos_calculados(self):
        corpo = self._observabilidade([dict(ITEM_OK, numero=1), dict(ITEM_INVALIDO, numero=2),
                                       dict(ITEM_OK, numero=3, baseCalculo=Decimal("10"))])
        estados = [o["estadoItem"] for o in corpo["objetos"]]
        self.assertEqual(estados[0], "CALCULADO")
        self.assertNotIn(estados[1], SUCESSO)
        self.assertEqual(corpo["total"]["tribCalc"]["IBSCBSTot"]["vBCIBSCBS"], "35.00")
        so_calculados = self._observabilidade([dict(ITEM_OK, numero=1),
                                               dict(ITEM_OK, numero=3, baseCalculo=Decimal("10"))])
        self.assertEqual(corpo["total"], so_calculados["total"])

    def test_sem_calculados_total_some(self):
        corpo = self._observabilidade([dict(ITEM_INVALIDO, numero=1), dict(ITEM_INVALIDO, numero=2)])
        self.assertEqual(len(corpo["objetos"]), 2)
        self.assertNotIn("total", corpo)

    def test_regime_geral_totaliza_todos(self):
        resposta = bateria.responder({"metodo": "POST", "caminho": REGIME_GERAL, "corpo": _operacao(
            [dict(ITEM_OK, numero=2), dict(ITEM_OK, numero=1, baseCalculo=Decimal("10"))])})
        self.assertEqual(resposta["status"], 200)
        corpo = resposta["corpo"]
        self.assertEqual(list(corpo), ["objetos", "total"])
        self.assertEqual([o["nObj"] for o in corpo["objetos"]], [1, 2])
        total = corpo["total"]["tribCalc"]["IBSCBSTot"]
        self.assertNotIn("ISTot", corpo["total"]["tribCalc"])
        self.assertEqual(list(total), ["vBCIBSCBS", "gIBS", "gCBS", "gMono"])
        self.assertEqual(total["vBCIBSCBS"], "35.00")
        self.assertEqual(total["gMono"], dict.fromkeys(
            ("vIBSMono", "vCBSMono", "vIBSMonoReten", "vCBSMonoReten", "vIBSMonoRet", "vCBSMonoRet"), "0.00"))


class CabecalhosTest(unittest.TestCase):
    """Os cabeçalhos dos controllers: versão da calculadora e da base, e x-warning-dados-simulados."""

    @classmethod
    def setUpClass(cls):
        cls.base = rr_base.Base()

    @classmethod
    def tearDownClass(cls):
        cls.base.fechar()

    def _cabecalhos(self, funcao, corpo):
        status, _corpo, cabecalhos = funcao(corpo, self.base)
        return status, cabecalhos

    def test_versao_e_aviso_6_a_partir_de_2027(self):
        status, cabecalhos = self._cabecalhos(rr_motor.regime_geral, _operacao(
            [dict(ITEM_OK, numero=1, aliquotasNominais=NOMINAIS)], dh="2027-01-01T10:00:00-03:00"))
        self.assertEqual(status, 200)
        self.assertEqual(cabecalhos["X-CALC-APP-VERSION"], self.base.meta()["calculadora"]["versao"])
        self.assertEqual(cabecalhos["X-CALC-DB-VERSION"], self.base.versao_base()["versao"])
        self.assertEqual(cabecalhos["x-warning-dados-simulados"], "6")

    def test_2026_sem_aviso_e_com_cst_010_aviso_5(self):
        status, cabecalhos = self._cabecalhos(rr_motor.observabilidade, _operacao([dict(ITEM_OK, numero=1)]))
        self.assertEqual(status, 200)
        self.assertNotIn("x-warning-dados-simulados", cabecalhos)
        self.assertIn("X-CALC-APP-VERSION", cabecalhos)
        for cst in ("010", "220", "221"):
            status, cabecalhos = self._cabecalhos(rr_motor.observabilidade, _operacao(
                [dict(ITEM_OK, numero=1), dict(ITEM_OK, numero=2, cst=cst)]))
            self.assertEqual((status, cabecalhos.get("x-warning-dados-simulados")), (200, "5"), cst)

    def test_erro_sem_cabecalhos(self):
        corpo = _operacao([dict(ITEM_INVALIDO, numero=1)])
        status, cabecalhos = self._cabecalhos(rr_motor.regime_geral, corpo)
        self.assertNotEqual(status, 200)
        self.assertEqual(cabecalhos, {})

    def test_falha_de_escrita_leva_os_cabecalhos(self):
        """A oficial respondeu o 500 "Failed to write request" com os cabeçalhos de versão e o aviso 6."""
        item = dict(ITEM_OK, numero=1, ncm="22030000", baseCalculo=Decimal("100"), quantidade=1, unidade="UN",
                    impostoSeletivo={"cst": "000", "cClassTrib": "000001", "baseCalculo": Decimal("100"),
                                     "quantidade": 1, "unidade": "GARRAFA"},
                    aliquotasNominais=dict(NOMINAIS, impostoSeletivo={"adValorem": 10}))
        for funcao in (rr_motor.regime_geral, rr_motor.observabilidade):
            status, cabecalhos = self._cabecalhos(funcao, _operacao([item], dh="2027-01-01T10:00:00-03:00"))
            self.assertEqual(status, 500)
            self.assertEqual(cabecalhos["x-warning-dados-simulados"], "6")
            self.assertIn("X-CALC-DB-VERSION", cabecalhos)


class ImpostoSeletivoTest(unittest.TestCase):
    """O grupo impostoSeletivo é obrigatório quando o NCM tem incidência do IS na data."""

    DATA = date(2027, 1, 1)

    @staticmethod
    def _ncm_da_cerveja(base) -> str:
        """O primeiro NCM de oito dígitos da posição 2203 (cerveja) com alíquota do IS na data."""
        linhas = base._con.execute(
            "SELECT NCM_CD FROM NCM WHERE NCM_CD LIKE '2203%' AND LENGTH(NCM_CD) = 8 ORDER BY NCM_CD").fetchall()
        for (ncm,) in linhas:
            if (base.existe_ncm_ad_valorem(ncm, rr_base.TRIBUTO_IS, ImpostoSeletivoTest.DATA)
                    or base.buscar_aliquota_ad_rem(ncm, rr_base.TRIBUTO_IS, ImpostoSeletivoTest.DATA) is not None):
                return ncm
        raise AssertionError("a base não tem NCM de cerveja com Imposto Seletivo em 2027-01-01")

    def test_is_sem_grupo_em_2027_da_erro_oficial(self):
        with rr_base.Base() as base:
            ncm = self._ncm_da_cerveja(base)
        esperado = next(c for c in ESPERADAS["casos"] if c["id"].startswith("is-sem-grupo-2203")
                        and c["id"].endswith("-obs"))
        erro_esperado = esperado["resposta"]["corpo"]["objetos"][0]["erros"][0]
        resposta = bateria.responder({"metodo": "POST", "caminho": "/calculadora/observabilidade/regime-geral",
                                      "corpo": {
            "id": "is", "versao": "0.0.1", "dhFatoGerador": "2027-01-01T10:00:00-03:00", "municipio": 4314902,
            "uf": "RS", "itens": [{"numero": 1, "ncm": ncm, "cst": "000", "cClassTrib": "000001",
                                   "baseCalculo": Decimal("100"), "quantidade": 1, "unidade": "UN",
                                   "aliquotasNominais": {"cbs": Decimal("8.8"), "ibsEstadual": Decimal("8.85"),
                                                         "ibsMunicipal": Decimal("8.85")}}]}})
        self.assertEqual(resposta["status"], 200)
        objeto = resposta["corpo"]["objetos"][0]
        self.assertEqual(objeto["estadoItem"], "INCONSISTENCIA_ENTRADA")
        erro = objeto["erros"][0]
        self.assertEqual(erro["code"], erro_esperado["code"])
        self.assertEqual(erro["pointer"], erro_esperado["pointer"])
        self.assertEqual(erro["detail"], f"Dados do Imposto Seletivo devem ser informados para NCM {ncm} e data "
                                         "2027-01-01")


    def test_utrib_que_o_serializer_recusa_derruba_a_resposta(self):
        """StringUTribSerializer: uTrib com mais de 6 caracteres impede a escrita da resposta inteira (500 do
        Spring), nos dois endpoints, como a oficial respondeu na conferência da Task 9."""
        item = {"numero": 1, "ncm": "22030000", "cst": "000", "cClassTrib": "000001", "baseCalculo": Decimal("100"),
                "quantidade": 1, "unidade": "UN",
                "impostoSeletivo": {"cst": "000", "cClassTrib": "000001", "baseCalculo": Decimal("100"),
                                    "quantidade": 1, "unidade": "GARRAFA"},
                "aliquotasNominais": {"cbs": Decimal("8.8"), "ibsEstadual": Decimal("8.85"),
                                      "ibsMunicipal": Decimal("8.85"), "impostoSeletivo": {"adValorem": 10}}}
        for caminho in ("/calculadora/regime-geral", "/calculadora/observabilidade/regime-geral"):
            resposta = bateria.responder({"metodo": "POST", "caminho": caminho, "corpo": {
                "id": "is", "versao": "0.0.1", "dhFatoGerador": "2027-01-01T10:00:00-03:00", "municipio": 3550308,
                "uf": "SP", "itens": [item]}})
            self.assertEqual(resposta, {"status": 500, "corpo": {
                "detail": "Failed to write request", "instance": "/api" + caminho, "status": 500,
                "title": "Internal Server Error"}}, caminho)


class ExcecaoTecnicaTest(unittest.TestCase):
    """Exceção sem código no cálculo de um item: CAL-999 só nesse item (/observabilidade) e o 500 genérico
    (/regime-geral), como ItemCollector.addErro e ApiExceptionHandler.handleGenericException."""

    ITEM = {"cst": "000", "cClassTrib": "000001", "baseCalculo": Decimal("25.0"), "ncm": "84713012"}

    def _requisicao(self, caminho):
        itens = [dict(self.ITEM, numero=1), dict(self.ITEM, numero=2)]
        return {"metodo": "POST", "caminho": caminho, "corpo": {
            "id": "falha", "versao": "0.0.1", "dhFatoGerador": "2026-10-20T10:00:00-03:00", "municipio": 3550308,
            "uf": "SP", "tpDoc": 55, "itens": itens}}

    def _falhar_no_item_2(self):
        original = rr_motor.rr_calculo.calcular_cbs_ibs

        def calcular(proc, tributo, base, *args):
            if proc.item.numero == 2:
                raise RuntimeError("falha forçada no teste")
            return original(proc, tributo, base, *args)
        return mock.patch.object(rr_motor.rr_calculo, "calcular_cbs_ibs", calcular)

    def test_observabilidade_cal_999_so_no_item(self):
        with self._falhar_no_item_2():
            resposta = bateria.responder(self._requisicao("/calculadora/observabilidade/regime-geral"))
        self.assertEqual(resposta["status"], 200)
        um, dois = resposta["corpo"]["objetos"]
        self.assertEqual((um["nObj"], um["estadoItem"]), (1, "CALCULADO"))
        self.assertIn("IBSCBS", um["tribCalc"])
        self.assertEqual(dois, {"nObj": 2, "estadoItem": "FALHA_TECNICA", "erros": [{
            "type": "http://127.0.0.1/api/calculadora/observabilidade/erros?codigo=CAL-999",
            "title": "Erro de sistema não previsto", "code": "CAL-999", "pointer": "#/itens/1",
            "detail": "falha forçada no teste"}]})

    def test_regime_geral_500_generico(self):
        with self._falhar_no_item_2():
            resposta = bateria.responder(self._requisicao("/calculadora/regime-geral"))
        self.assertEqual(resposta["status"], 500)
        corpo = resposta["corpo"]
        self.assertEqual((corpo["title"], corpo["detail"], corpo["type"]), (
            "Erro de sistema não previsto", "falha forçada no teste",
            "http://127.0.0.1/errors/erro-de-sistema-nao-previsto"))


class ProjecaoTest(unittest.TestCase):
    def test_tira_total_e_memoria_so_das_respostas_200(self):
        resposta = {"status": 200, "corpo": {"objetos": [{"nObj": 1, "tribCalc": {
            "IBSCBS": {"vBC": Decimal("10.00"), "memoriaCalculo": {"x": 1}}}}], "total": {"v": 1}}}
        self.assertEqual(projetar(resposta), {"status": 200, "corpo": {"objetos": [{"nObj": 1, "tribCalc": {
            "IBSCBS": {"vBC": Decimal("10.00")}}}]}})
        erro = {"status": 422, "corpo": {"total": 1, "memoriaCalculo": 2}}
        self.assertEqual(projetar(erro), erro)

    def test_diferenca_de_estado_aparece_na_projecao(self):
        a = {"status": 200, "corpo": {"objetos": [{"nObj": 1, "estadoItem": "CALCULADO"}]}}
        b = copy.deepcopy(a)
        b["corpo"]["objetos"][0]["estadoItem"] = "FALHA_TECNICA"
        self.assertTrue(receita_paridade.comparar(projetar(a), projetar(b), IGNORAR))


class ErrosTest(unittest.TestCase):
    def test_catalogo_tem_os_codigos_do_java(self):
        reg = [f"REG-{n:03d}" for n in range(1, 39) if n != 17]
        cal = [f"CAL-{n:03d}" for n in range(1, 13)] + ["CAL-999"]
        val = [f"VAL-{n:03d}" for n in range(0, 16)]
        for codigo in reg + cal + val:
            self.assertIsNotNone(rr_erros.descrever_erro(codigo), codigo)
        self.assertIsNone(rr_erros.descrever_erro("REG-017"))
        self.assertIsNone(rr_erros.descrever_erro("reg-001"))
        self.assertIsNone(rr_erros.descrever_erro(None))

    def test_descrever_erro(self):
        self.assertEqual(rr_erros.descrever_erro("REG-019"), {
            "codigo": "REG-019", "titulo": "Município não encontrado",
            "descricao": "O código do município informado não foi localizado na base de dados."})
        self.assertEqual(rr_erros.descrever_erro("VAL-010")["titulo"], "Quantidade de dígitos inválida")

    def test_status_pela_hierarquia_java(self):
        casos = {"REG-018": 422, "REG-019": 404, "REG-020": 404, "REG-021": 400, "REG-024": 400,
                 "REG-028": 422, "CAL-001": 404, "CAL-007": 422, "CAL-999": 500}
        for codigo, status in casos.items():
            self.assertEqual(rr_erros.ErroRR(codigo, "x").status_http, status, codigo)
        self.assertEqual(rr_erros.ErroRR("CAL-007", "x").estado_item, "NAO_IMPLEMENTADO")
        self.assertEqual(rr_erros.ErroRR("CAL-001", "x").estado_item, "FALHA_TECNICA")
        self.assertEqual(rr_erros.ErroRR("REG-002", "x").ponteiro, "ncm")


class EntradaTest(unittest.TestCase):
    CORPO = {"id": "a", "versao": "1", "dhFatoGerador": "2027-01-01T10:00:00-03:00", "municipio": " 4314902 ",
             "uf": "RS", "itens": [{"numero": Decimal("1.9"), "cst": 0, "cClassTrib": "000001", "baseCalculo": "",
                                    "quantidade": Decimal("1E+3"), "nbs": None}]}

    def test_leitura_tolerante_como_o_jackson(self):
        op = rr_entrada.ler_operacao(copy.deepcopy(self.CORPO))
        self.assertEqual(op.municipio, 4314902)
        self.assertEqual(op.dh_fato_gerador.data.isoformat(), "2027-01-01")
        item = op.itens[0]
        self.assertEqual((item.numero, item.cst, item.base_calculo, item.nbs), (1, "0", None, None))
        self.assertEqual(str(item.quantidade), "1E+3")

    def test_corpo_ilegivel(self):
        with self.assertRaises(rr_entrada.CorpoIlegivel) as ctx:
            rr_entrada.ler_operacao({"municipio": "abc"})
        self.assertEqual(str(ctx.exception), 'JSON parse error: Cannot deserialize value of type `java.lang.Long` '
                                             'from String "abc": not a valid `java.lang.Long` value')

    def test_bean_validation_na_ordem_das_anotacoes(self):
        corpo = copy.deepcopy(self.CORPO)
        corpo["itens"][0]["cst"] = "0a"
        erros = rr_entrada.validar_bean(rr_entrada.ler_operacao(corpo))
        self.assertEqual([(e.codigo, e.ponteiro) for e in erros],
                         [("VAL-009", "itens[0].cst"), ("VAL-004", "itens[0].cst")])
        self.assertEqual(erros[0].detalhe, "Campo 'itens[0].cst' Informar somente dígitos, recebido: '0a'")


class CalcCLITest(unittest.TestCase):
    """calc.py: a porta em português, os cenários de alíquota, as recusas de escopo e a linha de comando."""

    def operacao(self, **mudancas):
        op = {"id": "t1", "documento": "NF-e", "uf": "SP", "municipio": 3550308, "data": "2027-03-15",
              "itens": [{"descricao": "notebook", "ncm": "84713012", "cst": "000", "classificacao": "000001",
                         "valor": Decimal("100.00")}]}
        op.update(mudancas)
        return op

    def aliquotas(self, op):
        return [i.get("aliquotasNominais") for i in op["itens"]]

    def test_cenario_padrao_em_2027(self):
        op = rr_calc.normalizar(self.operacao(), "em-2027")
        self.assertEqual(op["versao"], "1.0.0")
        self.assertEqual(op["tpDoc"], 55)
        self.assertEqual(op["dhFatoGerador"], "2027-03-15T12:00:00-03:00")
        self.assertEqual(op["itens"][0]["baseCalculo"], Decimal("100.00"))
        self.assertEqual(op["itens"][0]["cClassTrib"], "000001")
        self.assertEqual(op["itens"][0]["numero"], 1)
        a = self.aliquotas(op)[0]
        self.assertEqual((str(a["cbs"]), str(a["ibsEstadual"]), str(a["ibsMunicipal"])), ("11.3", "0.05", "0.05"))

    def test_data_padrao_2027_01_01(self):
        op = self.operacao()
        del op["data"]
        r = rr_calc.normalizar(op, "em-2027")
        self.assertTrue(r["dhFatoGerador"].startswith("2027-01-01T"))
        self.assertIsNotNone(r["itens"][0]["aliquotasNominais"])

    def test_reforma_completa(self):
        a = self.aliquotas(rr_calc.normalizar(self.operacao(), "reforma-completa"))[0]
        self.assertEqual((str(a["cbs"]), str(a["ibsEstadual"]), str(a["ibsMunicipal"])), ("8.8", "8.85", "8.85"))

    def test_aliquota_informada_vence_o_cenario(self):
        op = self.operacao()
        op["itens"][0]["aliquotas"] = {"cbs": Decimal("1.5"), "ibsEstadual": Decimal("2"), "ibsMunicipal": 0}
        op["itens"].append(dict(op["itens"][0], numero=2))
        del op["itens"][1]["aliquotas"]
        r = rr_calc.normalizar(op, "reforma-completa")
        self.assertEqual(r["itens"][0]["aliquotasNominais"]["cbs"], Decimal("1.5"))
        self.assertEqual(str(r["itens"][1]["aliquotasNominais"]["cbs"]), "8.8")

    def test_antes_de_2027_nao_leva_aliquota(self):
        for data in ("2026-12-31", "31/12/2026", "2025-06-01"):
            with self.subTest(data):
                op = rr_calc.normalizar(self.operacao(data=data), "em-2027")
                self.assertNotIn("aliquotasNominais", op["itens"][0])

    def test_cenario_inexistente_recusado(self):
        with self.assertRaises(rr_calc.Recusa):
            rr_calc.normalizar(self.operacao(), "nenhum")

    def test_recusa_nbs_e_nfse(self):
        op = self.operacao()
        op["itens"][0]["nbs"] = "1.0101.10.00"
        with self.assertRaises(rr_calc.Recusa) as ctx:
            rr_calc.normalizar(op, "em-2027")
        self.assertIn("NBS", str(ctx.exception))
        with self.assertRaises(rr_calc.Recusa) as ctx:
            rr_calc.normalizar(self.operacao(documento="NFS-e"), "em-2027")
        self.assertIn("NFS-e", str(ctx.exception))
        for documento in (57, "CT-e", 99):
            with self.subTest(documento):
                with self.assertRaises(rr_calc.Recusa) as ctx:
                    rr_calc.normalizar(self.operacao(documento=documento), "em-2027")
                self.assertIn("55", str(ctx.exception))
        # na porta técnica: tpDoc 57 e NBS também ficam de fora
        for bruto in ({"tpDoc": 57, "itens": []}, {"tpDoc": 55, "itens": [{"nbs": "1.0101.10.00"}]}):
            with self.subTest(bruto):
                with self.assertRaises(rr_calc.Recusa):
                    rr_calc.verificar_escopo(bruto)

    def test_nbs_nulo_vale_como_ausente_e_tpdoc_ausente_passa(self):
        op = self.operacao()
        del op["documento"]
        op["itens"][0]["nbs"] = None
        r = rr_calc.normalizar(op, "em-2027")
        self.assertNotIn("tpDoc", r)
        rr_calc.verificar_escopo({"tpDoc": 65, "itens": [{"nbs": None}], "gCompraGov": None})

    def test_recusa_compra_gov(self):
        op = self.operacao(gCompraGov={"tpEnteGov": 1, "pRedutor": 5})
        with self.assertRaises(rr_calc.Recusa) as ctx:
            rr_calc.normalizar(op, "em-2027")
        self.assertIn("gCompraGov", str(ctx.exception))
        with self.assertRaises(rr_calc.Recusa):
            rr_calc.verificar_escopo({"tpDoc": 55, "gCompraGov": {"tpEnteGov": 1}})

    def test_ncm_7_digitos_repassado_como_veio(self):
        op = self.operacao()
        op["itens"][0]["ncm"] = "8471301"
        self.assertEqual(rr_calc.normalizar(op, "em-2027")["itens"][0]["ncm"], "8471301")

    def test_recusa_sai_antes_do_motor(self):
        with rr_base.Base() as base, mock.patch.object(rr_motor, "observabilidade") as motor:
            with self.assertRaises(rr_calc.Recusa):
                rr_calc.executar_operacao(self.operacao(documento="NFS-e"), base)
        motor.assert_not_called()

    def test_conta_de_ponta_a_ponta_em_2027_e_simulada(self):
        with rr_base.Base() as base:
            saida = rr_calc.executar_operacao(self.operacao(), base)
        self.assertEqual(saida["status"], 200)
        self.assertTrue(saida["simulado"])
        self.assertIn("simulado", saida["aviso"])
        self.assertEqual(saida["cenario"]["cenario"], "em-2027")
        self.assertEqual(saida["versao"]["calculadora"], "1.7.2")
        self.assertEqual(saida["corpo"]["objetos"][0]["estadoItem"], "CALCULO_SIMULADO")

    def test_conta_em_2026_sem_cenario_e_sem_aviso_de_simulado(self):
        with rr_base.Base() as base:
            saida = rr_calc.executar_operacao(self.operacao(data="2026-06-01"), base)
        self.assertEqual(saida["status"], 200)
        self.assertFalse(saida["simulado"])
        self.assertNotIn("cenario", saida)

    def test_porta_tecnica_roda_como_veio(self):
        bruto = {"id": "x", "versao": "1.0.0", "dhFatoGerador": "2027-03-15T10:00:00-03:00", "municipio": 3550308,
                 "uf": "SP", "tpDoc": 55,
                 "itens": [{"numero": 1, "cst": "000", "cClassTrib": "000001", "baseCalculo": Decimal("12.0"),
                            "ncm": "84713012"}]}
        copia = copy.deepcopy(bruto)
        with rr_base.Base() as base:
            saida = rr_calc.executar_operacao(bruto, base, porta="tecnica")
        self.assertEqual(bruto, copia)
        self.assertNotIn("cenario", saida)
        self.assertTrue(saida["simulado"])

    def test_executar_mantem_o_contrato_da_bateria(self):
        requisicao = {"metodo": "POST", "caminho": "/calculadora/observabilidade/regime-geral",
                      "corpo": rr_calc.normalizar(self.operacao(), "em-2027")}
        resposta = rr_calc.executar(requisicao)
        self.assertEqual(set(resposta), {"status", "corpo"})
        status, _corpo, cabecalhos = rr_calc.executar_completo(requisicao)
        self.assertEqual(status, resposta["status"])
        self.assertEqual(cabecalhos["x-warning-dados-simulados"], "6")

    def test_aliquotas_json(self):
        dados = rr_calc.ler_aliquotas()
        self.assertEqual((dados["version"], dados["checked_at"]), ("2026-11-22", "2026-11-22"))
        em = dados["cenarios"]["em-2027"]
        self.assertEqual((str(em["cbs"]), str(em["ibs_estadual"]), str(em["ibs_municipal"])), ("11.3", "0.05", "0.05"))
        rc = dados["cenarios"]["reforma-completa"]
        self.assertEqual((str(rc["cbs"]), str(rc["ibs_estadual"]), str(rc["ibs_municipal"])), ("8.8", "8.85", "8.85"))
        self.assertTrue(rc["hipotese_cd2"])
        self.assertEqual(len(dados["fontes"]), 3)
        self.assertEqual(dados["lei"], "Lei Complementar 214/2025")
        texto = rr_calc.ARQUIVO_ALIQUOTAS.read_text(encoding="utf-8")
        self.assertNotIn("\u2014", texto)
        self.assertNotIn("\u2013", texto)

    def chamar(self, argv):
        saida = io.StringIO()
        with contextlib.redirect_stdout(saida):
            codigo = rr_calc.main(argv)
        return codigo, json.loads(saida.getvalue())

    def test_cli_cenarios_versao_erro(self):
        codigo, dados = self.chamar(["--cenarios"])
        self.assertEqual((codigo, dados["padrao"]), (0, "em-2027"))
        codigo, dados = self.chamar(["--versao"])
        self.assertEqual((codigo, dados["corpo"]["versaoApp"]), (0, "1.7.2"))
        codigo, dados = self.chamar(["--erro", "REG-001"])
        self.assertEqual((codigo, dados["status"], dados["corpo"]["codigo"]), (0, 200, "REG-001"))
        codigo, dados = self.chamar(["--erro", "XXX-999"])
        self.assertEqual((codigo, dados["status"]), (0, 404))

    def test_cli_recusa_sai_com_2(self):
        import tempfile
        with tempfile.TemporaryDirectory() as pasta:
            arquivo = Path(pasta) / "op.json"
            arquivo.write_text(json.dumps(self.operacao(documento="NFS-e"), default=str), encoding="utf-8")
            codigo, dados = self.chamar([str(arquivo)])
            self.assertEqual(codigo, 2)
            self.assertIn("NFS-e", dados["errors"][0])
            arquivo.write_text(json.dumps(self.operacao(), default=str), encoding="utf-8")
            codigo, dados = self.chamar([str(arquivo), "--cenario", "reforma-completa"])
            self.assertEqual((codigo, dados["status"]), (0, 200))
            self.assertEqual(dados["cenario"]["cenario"], "reforma-completa")
            self.assertTrue(dados["cenario"]["hipotese_cd2"])

    def test_checar_versao_sem_internet_sai_0(self):
        original = rr_calc._abrir
        self.addCleanup(setattr, rr_calc, "_abrir", original)

        def sem_rede(pedido, timeout=None):
            raise urllib.error.URLError("sem rede")
        rr_calc._abrir = sem_rede
        codigo, dados = self.chamar(["--checar-versao"])
        self.assertEqual(codigo, 0)
        self.assertEqual(set(dados), {"consultado", "ha_versao_nova", "ha_tabela_nova", "calculadora", "mensagem"})
        self.assertFalse(dados["consultado"])
        self.assertTrue(dados["mensagem"])

    def test_checar_versao_usa_o_repositorio_do_regime_regular(self):
        pedidos = []

        def falso(pedido, timeout=None):
            pedidos.append(pedido.full_url)
            raise urllib.error.URLError("sem rede")
        original = rr_calc._abrir
        self.addCleanup(setattr, rr_calc, "_abrir", original)
        rr_calc._abrir = falso
        rr_calc.checar_versao({})
        self.assertEqual(pedidos, ["https://api.github.com/repos/ReceitaFederal/calculadora-rtc-regime-regular/"
                                   "releases/latest"])
        self.assertIn("calculadora-rtc-regime-regular/commits?path=flyway/sql", rr_calc.URL_TABELAS)

    def test_checar_versao_compara_tag_e_tabelas(self):
        meta = {"calculadora": {"versao": "1.7.2"}, "migracoes": {"commit": "a" * 40},
                "base": {"versao": "V0073", "data": "2026-11-09"}, "conferencia": {"data": "2026-11-24"}}

        class Resp:
            def __init__(self, texto):
                self.texto = texto

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def read(self, limite=-1):
                return self.texto.encode()[:limite]

        def falso(pedido, timeout=None):
            if pedido.full_url == rr_calc.URL_VERSAO:
                return Resp(json.dumps({"tag_name": "1.8.0"}))
            return Resp(json.dumps([{"sha": "b" * 40, "commit": {"committer": {"date": "2026-12-01T10:00:00Z"}}}]))
        original = rr_calc._abrir
        self.addCleanup(setattr, rr_calc, "_abrir", original)
        rr_calc._abrir = falso
        r = rr_calc.checar_versao(meta)
        self.assertEqual((r["consultado"], r["ha_versao_nova"], r["ha_tabela_nova"]), (True, True, True))


if __name__ == "__main__":
    unittest.main()
