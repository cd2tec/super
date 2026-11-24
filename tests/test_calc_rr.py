"""Testes do motor da skill cbs-ibs-regime-regular contra as respostas oficiais gravadas.

A paridade vai grupo a grupo: `paridade_do_grupo(grupo)` roda `bateria.responder` (que chama
`rr_calc.executar`) sobre os casos do grupo no esperadas.json e devolve os ids que divergem. Cada task do
plano liga os seus grupos em `GRUPOS_LIGADOS`.

Projeção. Até a Task 10, a comparação usa uma projeção das respostas (`PROJECAO = True`): o status HTTP,
o corpo inteiro das respostas de erro (status diferente de 200: título, tipo, detalhe, códigos e
ponteiros) e, nas respostas 200, tudo menos o `total` da operação e as `memoriaCalculo` (a memória de
cálculo e os totais só nascem na Task 10). Ficam então o `nObj`, o `estadoItem`, os `erros` de cada item
(código, título, tipo, ponteiro e detalhe) e os campos numéricos dos tributos. A Task 10 troca
`PROJECAO` para False, e a comparação passa a ser a do robô, inteira.

Parte de erro. Um grupo em `GRUPOS_SO_ERROS` é cobrado só no que não é sucesso (`divergencias_de_erro`):
as respostas de erro e os itens cujo `estadoItem` esperado não é CALCULADO nem CALCULO_SIMULADO têm de
sair iguais (dentro da projeção); os de sucesso têm de chegar ao cálculo sem erro de processamento (o
`rr_motor._calcular_item` levantava `CalculoNaoPortado`). Na Task 7 o grupo `nomenclatura` ficou
assim; desde a Task 8 nenhum grupo está nessa lista, e todo grupo ligado é cobrado inteiro.

As partes comparadas usam `receita_paridade.comparar` (Decimal pela forma escrita) com a lista `ignorar`
do config.json, como a conferência com a calculadora oficial.
"""
import copy
import importlib.util
import json
import sys
import unittest
from unittest import mock
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "core" / "cd2-super" / "skills" / "cbs-ibs-regime-regular"
PASTA = ROOT / "conferencia" / "cbs-ibs-regime-regular"

# A Task 10 troca para False: daí em diante a comparação é inteira, sem projeção.
PROJECAO = True
GRUPOS_LIGADOS = ["entrada", "nomenclatura", "cbs-ibs", "reducao", "diferimento", "tributacao-regular"]
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


if __name__ == "__main__":
    unittest.main()
