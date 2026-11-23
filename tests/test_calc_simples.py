"""Testes do motor da skill cbs-ibs-simples (simples_motor.py): a operação e os itens.

Conferem o motor contra as respostas gravadas da calculadora oficial do Simples Nacional (tag 1.0.2,
base V0073): os casos do grupo `global` (validação da operação), do grupo `item` (cálculo dos itens,
par de alíquotas e totais) e do grupo `erro` (catálogo VSN-).

As respostas oficiais foram gravadas chamando a calculadora em http://127.0.0.1:8082/api. Por isso o
teste passa `base_url="http://127.0.0.1"` (esquema://host, sem porta: HttpUtils.getBaseURL), que entra
no `type` de cada erro. O `instance` ("/api/calculadora/observabilidade/simples-nacional/nao-assistido")
é o padrão de `calcular`. O `timestamp` do erro fica fora da conferência (config.json, `ignorar`).
"""
import copy
import importlib.util
import json
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from urllib.parse import parse_qsl, urlsplit
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "core" / "cd2-super" / "skills" / "cbs-ibs-simples"
PASTA_CONFERENCIA = ROOT / "conferencia" / "cbs-ibs-simples"
BASE_URL_OFICIAL = "http://127.0.0.1"
INSTANCIA = "/api/calculadora/observabilidade/simples-nacional/nao-assistido"


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


simples_base = load_module("simples_base", SKILL / "scripts" / "simples_base.py")
simples_motor = load_module("simples_motor", SKILL / "scripts" / "simples_motor.py")
receita_paridade = load_module("receita_paridade", ROOT / "scripts" / "receita_paridade.py")
calc = load_module("calc_simples", SKILL / "scripts" / "calc.py")

Base = simples_base.Base
ESPERADAS = receita_paridade.ler_json((PASTA_CONFERENCIA / "esperadas.json").read_text(encoding="utf-8"))
CONFIG = json.loads((PASTA_CONFERENCIA / "config.json").read_text(encoding="utf-8"))
IGNORAR = CONFIG["ignorar"]
AGORA = datetime(2026, 11, 22, 12, 0, 0, tzinfo=timezone.utc)


def casos(grupo):
    return [c for c in ESPERADAS["casos"] if c["grupo"] == grupo]


class ComBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = Base()

    @classmethod
    def tearDownClass(cls):
        cls.base.fechar()

    def calcular(self, corpo, **extra):
        return simples_motor.calcular(copy.deepcopy(corpo), self.base, BASE_URL_OFICIAL, agora=AGORA, **extra)


LINHA = {
    "fatorR": True,
    "impedimentoSublimite": False,
    "anexoI": {"pCBSSN": "1.1612", "pIBSSN": "0.0129"},
    "anexoII": {"pCBSSN": "1.1184", "pIBSSN": "0.0121"},
    "anexoIII": {"pCBSSN": "2.2154", "pIBSSN": "0.0257",
                 "semRedistribuicaoISS": {"pCBSSN": "1.99", "pIBSSN": "0.0231"}},
    "anexoIV": {"pCBSSN": "2.0119", "pIBSSN": "0.022",
                "semRedistribuicaoISS": {"pCBSSN": "1.85", "pIBSSN": "0.0202"}},
    "anexoV": {"pCBSSN": "3.2534", "pIBSSN": "0.0363"},
}
ITEM = {"numero": 1, "cAtivSN": "01", "cClassTrib": "000001", "baseCalculo": "1000.00",
        "valorImpostoSeletivo": "0.00"}


def operacao(**campos):
    """Operação válida de NF-e do Simples (CRT 1) com os campos trocados por `campos`; None tira o campo."""
    corpo = {"id": "op-1", "versao": "3.0.0", "data": "2027-03-15", "tpDoc": "55", "CRT": "1",
             "aliquotasEfetivas": copy.deepcopy(LINHA), "itens": [copy.deepcopy(ITEM)]}
    corpo.update(campos)
    return {k: v for k, v in corpo.items() if v is not None}


class OperacaoTest(ComBase):
    def test_grupo_global_bate_com_a_oficial(self):
        self.assertEqual(len(casos("global")), 34)
        for caso in casos("global"):
            with self.subTest(caso["id"]):
                status, corpo = simples_motor.calcular(
                    caso["requisicao"]["corpo"], self.base, BASE_URL_OFICIAL, agora=AGORA)
                diferencas = receita_paridade.comparar(
                    caso["resposta"], {"status": status, "corpo": corpo}, IGNORAR)
                self.assertEqual(diferencas, [])

    def test_nenhum_caso_global_chega_aos_itens(self):
        for caso in casos("global"):
            with self.subTest(caso["id"]):
                status, corpo = simples_motor.calcular(
                    caso["requisicao"]["corpo"], self.base, BASE_URL_OFICIAL, agora=AGORA)
                self.assertEqual(status, 422)
                self.assertNotIn("objetos", corpo)

    def test_operacao_valida_chega_aos_itens(self):
        status, corpo = self.calcular(operacao())
        self.assertEqual(status, 200)
        self.assertEqual(corpo["objetos"][0]["estadoItem"], "CALCULADO")

    def test_catalogo_bate_com_a_oficial(self):
        self.assertEqual(len(casos("erro")), 53)
        for caso in casos("erro"):
            with self.subTest(caso["id"]):
                codigo = caso["requisicao"]["caminho"].split("codigo=", 1)[1]
                status, corpo = simples_motor.descrever_erro(codigo)
                diferencas = receita_paridade.comparar(
                    caso["resposta"], {"status": status, "corpo": corpo}, IGNORAR)
                self.assertEqual(diferencas, [])

    def test_catalogo_so_tem_os_codigos_da_oficial(self):
        oficiais = {c["requisicao"]["caminho"].split("codigo=", 1)[1]
                    for c in casos("erro") if c["resposta"]["status"] == 200}
        self.assertEqual(set(simples_motor.CODIGOS), oficiais)
        self.assertNotIn("VSN-008", simples_motor.CODIGOS)
        self.assertNotIn("VSN-009", simples_motor.CODIGOS)

    def test_consulta_de_erro_e_exata(self):
        for codigo in (None, "", "vsn-001", " VSN-001", "VSN-001 ", "VSN-998"):
            with self.subTest(codigo):
                self.assertEqual(simples_motor.descrever_erro(codigo), (404, None))

    def test_esperadas_sao_da_base_instalada(self):
        self.assertEqual(ESPERADAS["meta"]["base"]["hash"], self.base.meta()["base"]["hash"])

    def test_motor_nao_usa_numero_de_ponto_flutuante(self):
        fonte = (SKILL / "scripts" / "simples_motor.py").read_text(encoding="utf-8")
        self.assertNotIn("float(", fonte)
        self.assertNotIn("float ", fonte)

    def test_motor_nao_importa_a_pasta_scripts_do_repositorio(self):
        fonte = (SKILL / "scripts" / "simples_motor.py").read_text(encoding="utf-8")
        self.assertNotIn("receita_paridade", fonte)
        self.assertNotIn("receita_base", fonte)


class ContextoDaOperacaoTest(ComBase):
    """O ContextoOperacao é a interface da Tarefa 6: confere o que `calcular` guarda nele."""

    def contexto(self, corpo):
        dados = simples_motor._ler_corpo(copy.deepcopy(corpo))
        return simples_motor._preparar_operacao(dados, self.base, BASE_URL_OFICIAL, INSTANCIA, AGORA)

    def test_nfe_do_simples(self):
        ctx = self.contexto(operacao())
        self.assertEqual(ctx.tp_doc, 55)
        self.assertEqual(ctx.vocabulario, "CRT")
        self.assertEqual(ctx.data.texto, "2027-03-15")
        self.assertEqual(ctx.regime["codigo"], "SN_PLENO")
        self.assertTrue(ctx.calcula_tributos)
        self.assertEqual(ctx.informado.crt, 1)
        self.assertIsNone(ctx.tp_ente_gov)
        self.assertIsNone(ctx.fator_transicao)
        self.assertEqual(ctx.sigla_dfe, "NF-e")
        self.assertEqual(len(ctx.situacoes_iss), 10)
        self.assertEqual(ctx.avisos, [])
        self.assertEqual(len(ctx.dados.itens), 1)
        self.assertIs(ctx.base, self.base)

    def test_linha_de_aliquotas_fica_em_decimal_exato(self):
        linha = self.contexto(operacao()).linha
        self.assertEqual(linha.origem, "REQUISICAO")
        self.assertIs(linha.fator_r, True)
        self.assertFalse(linha.impedimento_sublimite)
        self.assertEqual(linha.anexo("I").par.p_cbssn, Decimal("1.1612"))
        self.assertIsNone(linha.anexo("I").sem_redistribuicao_iss)
        self.assertEqual(linha.anexo("III").sem_redistribuicao_iss.p_ibssn, Decimal("0.0231"))
        with self.assertRaises(ValueError):
            linha.anexo("VI")

    def test_linha_aceita_anexo_faltando_e_par_sem_vazio(self):
        corpo = operacao()
        del corpo["aliquotasEfetivas"]["anexoV"]
        corpo["aliquotasEfetivas"]["anexoIV"]["semRedistribuicaoISS"] = {"pCBSSN": " ", "pIBSSN": None}
        linha = self.contexto(corpo).linha
        self.assertIsNone(linha.anexo("V"))
        self.assertIsNone(linha.anexo("IV").sem_redistribuicao_iss)

    def test_compra_governamental_traz_o_fator_de_transicao(self):
        ctx = self.contexto(operacao(tpEnteGov="2"))
        self.assertEqual(ctx.tp_ente_gov, 2)
        self.assertEqual(ctx.fator_transicao, Decimal("0.0"))
        self.assertIsNone(self.contexto(operacao(tpEnteGov="1")).fator_transicao)

    def test_regime_que_nao_calcula_tributos_nao_le_a_linha(self):
        ctx = self.contexto(operacao(CRT="1", tpRegimeIBSCBS="1", aliquotasEfetivas=None))
        self.assertFalse(ctx.calcula_tributos)
        self.assertIsNone(ctx.linha)
        self.assertEqual(ctx.situacoes_iss, [])

    def test_nfse_usa_o_vocabulario_proprio(self):
        ctx = self.contexto(operacao(tpDoc="91", CRT=None, opSimpNac="3", regApIBSCBSSN="1", regApTribSN="1"))
        self.assertEqual(ctx.vocabulario, "NFSE")
        self.assertEqual(ctx.informado.op_simp_nac, 3)
        self.assertEqual(ctx.documento["maximoItens"], 1)

    def test_avisos_da_operacao(self):
        linha = copy.deepcopy(LINHA)
        linha["impedimentoSublimite"] = True
        ctx = self.contexto(operacao(aliquotasEfetivas=linha))
        self.assertEqual(ctx.avisos, [
            "Regime de apuração SN_PLENO (CRT=1) diverge do indicador de impedimento por sublimite da "
            "linha de alíquotas (true)"])
        ctx = self.contexto(operacao(tpDoc="91", CRT=None, opSimpNac="3", regApIBSCBSSN="2", regApTribSN="1"))
        self.assertEqual(ctx.avisos[-1], "regApIBSCBSSN 2 (excesso de sublimite) com regApTribSN 1: "
                         "incoerente \u2014 quem excede o sublimite tem o ISS fora do Simples Nacional")


class ValidacaoGlobalTest(ComBase):
    def erros(self, corpo):
        status, resposta = self.calcular(corpo)
        self.assertEqual(status, 422)
        return [(e["code"], e["pointer"], e["detail"]) for e in resposta["errors"]]

    def test_campos_globais_em_branco_na_ordem_do_java(self):
        corpo = operacao(id=" ", versao="", data=None, tpEnteGov="9", itens=[])
        self.assertEqual([e[:2] for e in self.erros(corpo)], [
            ("VSN-013", "#/id"), ("VSN-013", "#/versao"), ("VSN-012", "#/data"),
            ("VSN-024", "#/tpEnteGov"), ("VSN-011", "#/itens")])

    def test_data_segue_o_iso_local_date_estrito(self):
        for texto in ("2027-02-29", "2027-3-5", "20270315", "+2027-03-15", "10000-01-01", "2027-03-15T00:00",
                      "٢٠٢٧-03-15"):
            with self.subTest(texto):
                self.assertEqual(self.erros(operacao(data=texto)), [
                    ("VSN-012", "#/data", f"Data inválida, recebido: '{texto}' (esperado YYYY-MM-DD).")])
        # aparada, e bissexto vale
        for texto in (" 2028-02-29 ", "2027-03-15\t"):
            with self.subTest(texto):
                self.assertEqual(self.calcular(operacao(data=texto))[0], 200)

    def test_data_fora_das_vigencias_nao_tem_documento(self):
        for texto in ("0000-01-01", "-0001-01-01", "2026-12-31"):
            with self.subTest(texto):
                self.assertEqual([e[:2] for e in self.erros(operacao(data=texto))], [("VSN-015", "#/tpDoc")])

    def test_ano_acima_de_9999_e_falha_da_oficial(self):
        status, corpo = self.calcular(operacao(data="+10000-01-01"))
        self.assertEqual(status, 500)
        self.assertEqual(corpo["type"], "http://127.0.0.1/errors/erro-de-sistema-nao-previsto")
        self.assertEqual(corpo["detail"], "Index -1 out of bounds for length 4")

    def test_inteiro_tolerante_como_o_integer_value(self):
        # " 1" e "+1" valem; "1.0" e "01x" não
        self.assertEqual(self.calcular(operacao(CRT=" +1"))[0], 200)
        self.assertEqual(self.erros(operacao(CRT="1.0"))[0][0], "VSN-016")
        self.assertEqual(self.erros(operacao(CRT="99999999999"))[0][0], "VSN-016")

    def test_tp_doc_aparado_e_com_mais_e_aceito_na_mensagem_como_inteiro(self):
        erros = self.erros(operacao(tpDoc=" +77 "))
        self.assertEqual(erros, [("VSN-015", "#/tpDoc", "Tipo de documento fiscal (tpDoc) de código 77 ainda "
                                  "não possui tratamento implementado no cálculo do Simples Nacional.")])

    def test_numero_e_booleano_do_json_viram_texto(self):
        status, corpo = self.calcular(operacao(CRT=1, tpRegimeIBSCBS=True))
        self.assertEqual(status, 422)
        self.assertEqual(corpo["errors"][0]["detail"],
                         "Tipo de regime de apuração do IBS/CBS inválido, recebido: 'true' (esperado 1 ou 2).")
        self.assertEqual(self.erros(operacao(CRT=Decimal("1.0")))[0][0], "VSN-016")
        status, corpo = self.calcular(operacao(CRT=1, tpDoc=55))
        self.assertEqual(status, 200)
        self.assertEqual((corpo["oper"]["tpDoc"], corpo["oper"]["CRT"]), ("55", "1"))

    def test_vsn_039_so_quando_o_regime_calcula(self):
        self.assertEqual([e[0] for e in self.erros(operacao(aliquotasEfetivas=None))], ["VSN-039"])
        # SOMENTE_RECEITA: a fonte nem é consultada
        status, corpo = self.calcular(operacao(tpRegimeIBSCBS="1", aliquotasEfetivas=None))
        self.assertEqual(status, 200)
        self.assertNotIn("resolucaoAliquotas", corpo)

    def test_aliquota_fora_do_formato_na_ordem_dos_anexos(self):
        linha = copy.deepcopy(LINHA)
        linha["anexoV"]["pCBSSN"] = "-1.5"
        linha["anexoIII"]["semRedistribuicaoISS"]["pIBSSN"] = "1e3"
        linha["anexoI"]["pIBSSN"] = "1234.5"
        erros = self.erros(operacao(aliquotasEfetivas=linha))
        self.assertEqual([(c, p) for c, p, _ in erros], [
            ("VSN-007", "#/aliquotasEfetivas/anexoI/pIBSSN"),
            ("VSN-007", "#/aliquotasEfetivas/anexoIII/semRedistribuicaoISS/pIBSSN"),
            ("VSN-005", "#/aliquotasEfetivas/anexoV/pCBSSN")])
        self.assertEqual(erros[2][2], "O valor não pode ser negativo, recebido: '-1.5'.")
        self.assertTrue(erros[0][2].endswith("Informe até 3 dígitos inteiros e até 34 casas decimais, sem "
                                             "notação científica (ex.: 1.6839)."))

    def test_vsn_025_sem_vigencia_de_compra_governamental(self):
        # fora das vigências do parâmetro: nenhuma data de 2027 em diante falha, então simula a base
        from unittest import mock
        with mock.patch.object(self.base, "fator_transicao_compra_gov", return_value=None):
            erros = self.erros(operacao(tpEnteGov="3"))
        self.assertEqual(erros, [("VSN-025", "#/tpEnteGov", "Não há parametrização de transição de compra "
                                  "governamental vigente para a data '2027-03-15'.")])

    def test_nfse_mais_de_um_item_e_quantidade_exata(self):
        corpo = operacao(tpDoc="91", CRT=None, opSimpNac="3", regApIBSCBSSN="1", regApTribSN="1",
                         itens=[ITEM, ITEM, ITEM])
        self.assertEqual(self.erros(corpo), [("VSN-034", "#/itens",
                                              "O documento 91 admite exatamente 1 item, recebidos 3.")])

    def test_instancia_e_base_url_dos_erros(self):
        status, corpo = simples_motor.calcular(operacao(id=None), self.base, "https://exemplo.test",
                                               instancia="/x", agora=AGORA)
        self.assertEqual(status, 422)
        self.assertEqual(corpo["instance"], "/x")
        self.assertEqual(corpo["timestamp"], "2026-11-22T12:00:00Z")
        self.assertEqual(corpo["errors"][0]["type"],
                         "https://exemplo.test/calculadora/observabilidade/simples-nacional/erros?codigo=VSN-013")
        _, sem_base = simples_motor.calcular(operacao(id=None), self.base)
        self.assertTrue(sem_base["errors"][0]["type"].startswith("/calculadora/"))
        self.assertEqual(sem_base["instance"], INSTANCIA)


class CorpoIlegivelTest(ComBase):
    """Estrutura fora do contrato: HTTP 400, com as mensagens que a oficial devolveu."""

    def detalhe(self, corpo):
        status, resposta = simples_motor.calcular(corpo, self.base, BASE_URL_OFICIAL, agora=AGORA)
        self.assertEqual(status, 400)
        self.assertEqual(resposta["type"], "http://127.0.0.1/errors/mensagem-incompreensivel")
        self.assertEqual(resposta["title"], "Mensagem incompreensível")
        self.assertEqual(resposta["instance"], INSTANCIA)
        return resposta["detail"]

    def test_objeto_no_lugar_de_texto(self):
        self.assertEqual(self.detalhe({"id": {"a": 1}}),
                         "JSON parse error: Cannot deserialize value of type `java.lang.String` from Object "
                         "value (token `JsonToken.START_OBJECT`)")

    def test_lista_no_lugar_de_registro_e_objeto_no_lugar_de_lista(self):
        self.assertIn("AliquotasEfetivasInput` from Array value (token `JsonToken.START_ARRAY`)",
                      self.detalhe({"aliquotasEfetivas": [1]}))
        self.assertIn("ArrayList<br.gov.serpro.rtc.api.simplesnacional.model.input.ItemSimplesUnificadoInput>"
                      "` from Object value (token `JsonToken.START_OBJECT`)", self.detalhe({"itens": {"x": 1}}))
        self.assertIn("from String value (token `JsonToken.VALUE_STRING`)", self.detalhe({"itens": "x"}))

    def test_escalar_no_lugar_de_registro(self):
        self.assertIn("no String-argument constructor/factory method to deserialize from String value ('x')",
                      self.detalhe({"aliquotasEfetivas": "x"}))
        self.assertIn("no int/Int-argument constructor/factory method to deserialize from Number value (5)",
                      self.detalhe({"aliquotasEfetivas": {"anexoIII": {"semRedistribuicaoISS": 5}}}))

    def test_booleano(self):
        self.assertEqual(self.detalhe({"aliquotasEfetivas": {"fatorR": "abc"}}),
                         'JSON parse error: Cannot deserialize value of type `java.lang.Boolean` from String '
                         '"abc": only "true" or "false" recognized')
        self.assertIn("from Floating-point value (token `JsonToken.VALUE_NUMBER_FLOAT`)",
                      self.detalhe({"aliquotasEfetivas": {"fatorR": Decimal("1.5")}}))
        # aceitos: "TRUE" com espaços, inteiro, nulo
        for bom in (" TRUE", 2, None):
            with self.subTest(bom):
                status, _ = simples_motor.calcular(
                    {"aliquotasEfetivas": {"fatorR": bom}}, self.base, BASE_URL_OFICIAL, agora=AGORA)
                self.assertEqual(status, 422)

    def test_corpo_que_nao_e_registro(self):
        self.assertIn("OperacaoSimplesNaoAssistidaInput` from Array value", self.detalhe([1]))
        self.assertIn("no String-argument constructor", self.detalhe("x"))
        self.assertTrue(self.detalhe(None).startswith("Required request body is missing: "))

    def test_o_primeiro_defeito_na_ordem_do_json_vence(self):
        detalhe = self.detalhe({"itens": "x", "id": {"a": 1}})
        self.assertIn("ArrayList", detalhe)

    def test_campo_desconhecido_e_ignorado(self):
        status, _ = simples_motor.calcular({"naoExiste": {"a": [1]}}, self.base, BASE_URL_OFICIAL, agora=AGORA)
        self.assertEqual(status, 422)


class ItensTest(ComBase):
    """Os itens: classificação, atividade, par de alíquotas, a conta, os totais e o JSON da resposta."""

    def conferir(self, caso):
        status, corpo = simples_motor.calcular(caso["requisicao"]["corpo"], self.base, BASE_URL_OFICIAL, agora=AGORA)
        self.assertEqual(receita_paridade.comparar(caso["resposta"], {"status": status, "corpo": corpo}, IGNORAR),
                         [])
        return corpo

    def test_grupo_item_bate_com_a_oficial(self):
        self.assertEqual(len(casos("item")), 76)
        for caso in casos("item"):
            with self.subTest(caso["id"]):
                self.conferir(caso)

    def test_ordem_dos_campos_e_a_do_java(self):
        def chaves(a, b, caminho=""):
            if isinstance(a, dict) and isinstance(b, dict):
                self.assertEqual(list(b), list(a), caminho)
                for chave in a:
                    chaves(a[chave], b.get(chave), f"{caminho}/{chave}")
            elif isinstance(a, list) and isinstance(b, list):
                for i, (x, y) in enumerate(zip(a, b)):
                    chaves(x, y, f"{caminho}/{i}")
        for caso in casos("item"):
            with self.subTest(caso["id"]):
                _, corpo = simples_motor.calcular(caso["requisicao"]["corpo"], self.base, BASE_URL_OFICIAL,
                                                  agora=AGORA)
                chaves(caso["resposta"]["corpo"], corpo)

    def test_exemplos_oficiais_batem(self):
        exemplos = {c["id"]: c for c in casos("item") if c["id"] in ("exemplo-nfe", "exemplo-nfse")}
        self.assertEqual(set(exemplos), {"exemplo-nfe", "exemplo-nfse"})
        # o exemplo da NFS-e traz um NBS que não está na base V0073: a oficial responde VSN-050
        esperado = {"exemplo-nfe": ("CALCULADO", []), "exemplo-nfse": ("INCONSISTENCIA_ENTRADA", ["VSN-050"])}
        for id_, caso in exemplos.items():
            with self.subTest(id_):
                objeto = self.conferir(caso)["objetos"][0]
                self.assertEqual((objeto["estadoItem"], [e["code"] for e in objeto.get("erros", [])]),
                                 esperado[id_])

    def test_item_com_erro_nao_derruba_os_outros(self):
        caso = next(c for c in casos("item") if c["id"] == "item-com-erro-ao-lado-de-itens-calculados")
        corpo = self.conferir(caso)
        estados = {o["nObj"]: o["estadoItem"] for o in corpo["objetos"]}
        self.assertEqual(estados, {1: "CALCULADO", 2: "INCONSISTENCIA_ENTRADA", 3: "INCONSISTENCIA_ENTRADA",
                                   4: "CALCULADO"})
        com_erro = corpo["objetos"][2]
        self.assertEqual([e["code"] for e in com_erro["erros"]], ["VSN-002", "VSN-003"])
        self.assertNotIn("tribCalc", com_erro)
        # o total soma só os dois itens calculados
        self.assertEqual(corpo["total"]["vRBSNtot"], "1250.50")

    def test_total_e_a_soma_dos_valores_publicados(self):
        pares = {"vRBSNtot": "vRBSNitem", "vBC": "vBC", "vIBSSNtot": "vIBSSN", "vIBSSNtotPendSusp": "vIBSPendSusp",
                 "vCBSSNtot": "vCBSSN", "vCBSSNtotPendSusp": "vCBSPendSusp"}
        conferidos = 0
        for caso in casos("item"):
            status, corpo = simples_motor.calcular(caso["requisicao"]["corpo"], self.base, BASE_URL_OFICIAL,
                                                   agora=AGORA)
            if status != 200 or "total" not in corpo:
                continue
            with self.subTest(caso["id"]):
                tributos = [o["tribCalc"] for o in corpo["objetos"] if "tribCalc" in o]
                for total, campo in pares.items():
                    valores = [Decimal(t[campo]) for t in tributos if campo in t]
                    if valores:
                        self.assertEqual(Decimal(corpo["total"][total]), sum(valores), total)
                    else:
                        self.assertNotIn(total, corpo["total"])
                for t in tributos:
                    if "totalItem" in t:
                        devidos = [Decimal(t[c]) for c in ("vIBSSN", "vCBSSN") if c in t]
                        devidos += [Decimal(t["IS"]["valor"])] if "IS" in t else []
                        self.assertEqual(Decimal(t["totalItem"]), sum(devidos))
                conferidos += 1
        self.assertGreater(conferidos, 50)

    def test_objetos_em_ordem_de_numero_e_resolucao_na_ordem_de_entrada(self):
        itens = [dict(ITEM, numero=9), dict(ITEM, numero=2), dict(ITEM, numero=5, cAtivSN="02")]
        _, corpo = self.calcular(operacao(itens=itens))
        self.assertEqual([o["nObj"] for o in corpo["objetos"]], [2, 5, 9])
        self.assertEqual([r["numero"] for r in corpo["resolucaoAliquotas"]["itens"]], [9, 2, 5])

    def test_memoria_de_calculo_no_formato_brasileiro(self):
        # DecimalFormat("#,##0.00") e ("#,##0.00##") com os símbolos pt-BR, HALF_EVEN
        self.assertEqual(simples_motor._moeda_br(Decimal("9999999999999.99")), "9.999.999.999.999,99")
        self.assertEqual(simples_motor._moeda_br(Decimal("0")), "0,00")
        self.assertEqual(simples_motor._moeda_br(None), "0,00")
        self.assertEqual(simples_motor._percentual_br(Decimal("1.68393793783")), "1,6839")
        self.assertEqual(simples_motor._percentual_br(Decimal("1234.5")), "1.234,50")
        self.assertEqual(simples_motor._percentual_br(Decimal("0.00125")), "0,0012")


class LeituraDoItemTest(ComBase):
    """O item como o Jackson o lê (ItemSimplesUnificadoInput), com as respostas que a oficial deu."""

    def detalhe(self, itens, **campos):
        status, resposta = self.calcular(operacao(itens=itens, **campos))
        self.assertEqual(status, 400)
        self.assertEqual(resposta["title"], "Mensagem incompreensível")
        return resposta["detail"]

    def test_elemento_que_nao_e_objeto_e_400(self):
        tipo = "br.gov.serpro.rtc.api.simplesnacional.model.input.ItemSimplesUnificadoInput"
        construtor = f"JSON parse error: Cannot construct instance of `{tipo}` (although at least one Creator exists): "
        self.assertEqual(self.detalhe([ITEM, 5]), construtor + "no int/Int-argument constructor/factory method to "
                         "deserialize from Number value (5)")
        self.assertEqual(self.detalhe(["x"]), construtor + "no String-argument constructor/factory method to "
                         "deserialize from String value ('x')")
        self.assertEqual(self.detalhe([True]), construtor + "no boolean/Boolean-argument constructor/factory method "
                         "to deserialize from boolean value (true)")
        self.assertEqual(self.detalhe([Decimal("1.5")]), construtor + "no double/Double-argument constructor/"
                         "factory method to deserialize from Number value (1.5)")
        self.assertEqual(self.detalhe([[1]]), f"JSON parse error: Cannot deserialize value of type `{tipo}` from "
                         "Array value (token `JsonToken.START_ARRAY`)")
        for vazio in ("", " "):
            self.assertEqual(self.detalhe([vazio]), f'JSON parse error: Cannot coerce empty String ("") to `{tipo}` '
                             "value (but could if coercion was enabled using `CoercionConfig`)")

    def test_400_do_item_vence_o_422_da_operacao(self):
        self.assertIn("ItemSimplesUnificadoInput", self.detalhe([5], id=None, tpDoc="xx"))

    def test_elemento_nulo_vira_item_vazio(self):
        _, corpo = self.calcular(operacao(itens=[None]))
        objeto = corpo["objetos"][0]
        self.assertEqual(objeto["nObj"], 1)
        self.assertEqual([e["code"] for e in objeto["erros"]], ["VSN-010", "VSN-001", "VSN-003", "VSN-020"])

    def test_numero_como_o_integer_do_jackson(self):
        for bruto, lido in (("7", 7), (" +8 ", 8), (Decimal("9.9"), 9), (Decimal("-2.7"), -2), ("007", 7),
                            ("\u0663", 3), ("0000000000012", 12)):
            with self.subTest(bruto):
                _, corpo = self.calcular(operacao(itens=[dict(ITEM, numero=bruto)]))
                self.assertEqual(corpo["objetos"][0]["nObj"], lido)
        for nulo in ("", " ", "null", " null "):
            with self.subTest(nulo):
                _, corpo = self.calcular(operacao(itens=[dict(ITEM, numero=nulo)]))
                self.assertEqual(corpo["objetos"][0]["erros"][0]["code"], "VSN-010")

    def test_numero_fora_do_contrato_e_400(self):
        prefixo = "JSON parse error: Cannot deserialize value of type `java.lang.Integer` from "
        casos_400 = {
            True: prefixo + "Boolean value (token `JsonToken.VALUE_TRUE`)",
            "abc": prefixo + 'String "abc": not a valid `java.lang.Integer` value',
            " 2.5 ": prefixo + 'String "2.5": not a valid `java.lang.Integer` value',
            "1_0": prefixo + 'String "1_0": not a valid `java.lang.Integer` value',
            "NULL": prefixo + 'String "NULL": not a valid `java.lang.Integer` value',
            "99999999999999999999": prefixo + 'String "99999999999999999999": not a valid `java.lang.Integer` value',
            "-99999999999": prefixo + 'String "-99999999999": Overflow: numeric value (-99999999999) out of range '
                                      "of `java.lang.Integer` (-2147483648 -2147483647)",
            99999999999: "JSON parse error: Numeric value (99999999999) out of range of `int` "
                         "(-2147483648 - 2147483647)",
            Decimal("1e20"): "JSON parse error: Numeric value (1E+20) out of range of `int` (-2147483648 - 2147483647)",
        }
        for bruto, esperado in casos_400.items():
            with self.subTest(bruto):
                self.assertEqual(self.detalhe([dict(ITEM, numero=bruto)]), esperado)
        self.assertEqual(self.detalhe([dict(ITEM, numero={})]),
                         prefixo + "Object value (token `JsonToken.START_OBJECT`)")

    def test_campo_de_texto_do_item_fora_do_contrato_e_400(self):
        self.assertEqual(self.detalhe([dict(ITEM, cClassTrib={"a": 1})]),
                         "JSON parse error: Cannot deserialize value of type `java.lang.String` from Object value "
                         "(token `JsonToken.START_OBJECT`)")

    def test_ncm_e_nbs_aceitam_os_dois_nomes_e_o_ultimo_vale(self):
        _, corpo = self.calcular(operacao(itens=[dict(ITEM, NCM="x", ncm="12345678")]))
        erro = corpo["objetos"][0]["erros"][0]
        self.assertEqual((erro["code"], erro["pointer"]), ("VSN-049", "#/itens/0/ncm"))
        _, corpo = self.calcular(operacao(itens=[dict(ITEM, nbs="101011100", NBS="1")]))
        self.assertEqual(corpo["objetos"][0]["erros"][0]["code"], "VSN-051")


CALC_PY = SKILL / "scripts" / "calc.py"


def rodar_cli(*args, entrada=None, script=CALC_PY):
    return subprocess.run([sys.executable, "-I", str(script), *args], input=entrada, capture_output=True,
                          text=True, encoding="utf-8")


def saida_json(processo):
    return receita_paridade.ler_json(processo.stdout)


def argumentos_da_consulta(caminho):
    """Traduz o caminho de uma consulta oficial gravada nos argumentos da linha de comando."""
    partes = urlsplit(caminho)
    consulta = parse_qsl(partes.query, keep_blank_values=True)
    args = []
    resto = partes.path.rsplit("/atividades", 1)[1]
    if resto:
        args += ["--atividade", resto.lstrip("/")]
    for nome, valor in consulta:
        args += {"data": ["--data" if resto else "--atividades"], "anexos": ["--anexo"], "tpDoc": ["--tp-doc"],
                 "cClassTrib": ["--cclasstrib"]}[nome] + [valor]
    if not resto and not any(n == "data" for n, _ in consulta):
        args += ["--atividades", ""]  # a oficial recusa a data ausente
    return args


class CliTest(unittest.TestCase):
    def test_cli_roda_um_exemplo_oficial(self):
        caso = next(c for c in casos("item") if c["resposta"]["status"] == 200)
        entrada = receita_paridade.escrever_json(caso["requisicao"]["corpo"], None)
        processo = rodar_cli(entrada=entrada)
        self.assertEqual(processo.returncode, 0, processo.stderr)
        saida = saida_json(processo)
        self.assertEqual(receita_paridade.comparar(
            caso["resposta"], {"status": saida["status"], "corpo": saida["corpo"]}, IGNORAR), [])
        self.assertEqual(saida["versao"], {"calculadora": "1.0.2", "base": "V0073", "conferida_em": ""})
        self.assertNotIn("normalizacoes", saida)

    def test_cli_le_arquivo_e_mantem_os_valores_em_texto(self):
        caso = next(c for c in casos("item") if c["resposta"]["status"] == 200)
        with tempfile.TemporaryDirectory() as pasta:
            arquivo = Path(pasta) / "operacao.json"
            arquivo.write_text(receita_paridade.escrever_json(caso["requisicao"]["corpo"], None), encoding="utf-8")
            processo = rodar_cli("--input", str(arquivo))
        self.assertEqual(processo.returncode, 0, processo.stderr)
        total = json.loads(processo.stdout)["corpo"]["total"]
        self.assertTrue(all(isinstance(v, (str, int, bool, type(None), dict, list)) for v in total.values()))

    def test_cli_grupo_consulta_bate_com_a_oficial(self):
        # o `type` dos erros 400 muda de propósito: a CLI aponta para a documentação, não para 127.0.0.1
        ignorar = IGNORAR + ["type"]
        for caso in casos("consulta"):
            caminho = caso["requisicao"]["caminho"]
            if caminho == "/versao":
                continue
            with self.subTest(caso["id"], caminho=caminho):
                processo = rodar_cli(*argumentos_da_consulta(caminho))
                self.assertEqual(processo.returncode, 0, processo.stderr)
                saida = saida_json(processo)
                self.assertEqual(receita_paridade.comparar(
                    caso["resposta"], {"status": saida["status"], "corpo": saida["corpo"]}, ignorar), [])

    def test_cli_grupo_erro_bate_com_a_oficial(self):
        for caso in casos("erro")[:6]:
            codigo = caso["requisicao"]["caminho"].split("codigo=", 1)[1]
            with self.subTest(codigo):
                saida = saida_json(rodar_cli("--erro", codigo))
                self.assertEqual(receita_paridade.comparar(
                    caso["resposta"], {"status": saida["status"], "corpo": saida["corpo"]}, IGNORAR), [])

    def test_cli_codigo_de_erro_desconhecido_sai_0_com_404(self):
        processo = rodar_cli("--erro", "VSN-998")
        self.assertEqual(processo.returncode, 0)
        self.assertEqual(json.loads(processo.stdout)["status"], 404)

    def test_cli_versao(self):
        caso = next(c for c in casos("consulta") if c["requisicao"]["caminho"] == "/versao")
        processo = rodar_cli("--versao")
        self.assertEqual(processo.returncode, 0)
        corpo = saida_json(processo)["corpo"]
        for chave in ("versaoApp", "versaoDb", "descricaoVersaoDb", "dataVersaoDb"):
            self.assertEqual(corpo[chave], caso["resposta"]["corpo"][chave])

    def test_json_invalido_sai_2_com_errors(self):
        for entrada in ("{nao e json", "", '{"a": NaN}'):
            with self.subTest(entrada):
                processo = rodar_cli(entrada=entrada)
                self.assertEqual(processo.returncode, 2)
                self.assertIn("errors", json.loads(processo.stdout))
                self.assertNotIn("Traceback", processo.stderr)

    def test_arquivo_ilegivel_sai_2(self):
        processo = rodar_cli("--input", "/nao/existe/operacao.json")
        self.assertEqual(processo.returncode, 2)
        self.assertIn("errors", json.loads(processo.stdout))

    def test_json_que_nao_e_objeto_fica_com_a_resposta_do_motor(self):
        for entrada in ("[]", "3", '"x"', "null"):
            with self.subTest(entrada):
                processo = rodar_cli(entrada=entrada)
                self.assertEqual(processo.returncode, 0)
                self.assertEqual(json.loads(processo.stdout)["status"], 400)

    def test_base_ausente_sai_2_sem_traceback(self):
        with tempfile.TemporaryDirectory() as pasta:
            scripts = Path(pasta) / "scripts"
            scripts.mkdir()
            for nome in ("calc.py", "simples_motor.py", "simples_base.py", "numeros.py"):
                shutil.copy(SKILL / "scripts" / nome, scripts / nome)
            processo = rodar_cli("--versao", script=scripts / "calc.py")
        self.assertEqual(processo.returncode, 2)
        self.assertIn("errors", json.loads(processo.stdout))
        self.assertNotIn("Traceback", processo.stderr)

    def test_atividade_sem_data_tem_a_resposta_400_da_oficial(self):
        processo = rodar_cli("--atividade", "01")
        self.assertEqual(processo.returncode, 0)
        self.assertEqual(json.loads(processo.stdout)["status"], 400)

    def test_help_em_portugues(self):
        processo = rodar_cli("--help")
        self.assertEqual(processo.returncode, 0)
        self.assertIn("contador", processo.stdout)
        self.assertIn("--checar-versao", processo.stdout)


class CaminhoDaCliTest(ComBase):
    def test_a_funcao_da_cli_bate_com_as_oficiais_com_a_base_url_do_teste(self):
        for grupo in ("global", "item"):
            for caso in casos(grupo):
                with self.subTest(caso["id"]):
                    saida = calc.executar_operacao(copy.deepcopy(caso["requisicao"]["corpo"]), self.base,
                                                   base_url=BASE_URL_OFICIAL, agora=AGORA)
                    self.assertEqual(receita_paridade.comparar(
                        caso["resposta"], {"status": saida["status"], "corpo": saida["corpo"]}, IGNORAR), [])

    def test_type_dos_erros_aponta_para_a_documentacao(self):
        saida = calc.executar_operacao(operacao(id=None), self.base, agora=AGORA)
        self.assertEqual(saida["status"], 422)
        self.assertTrue(saida["corpo"]["errors"][0]["type"].startswith(calc.BASE_URL_CLI))
        self.assertNotIn("127.0.0.1", json.dumps(saida))
        self.assertTrue(calc.BASE_URL_CLI.startswith("https://github.com/ReceitaFederal/"))

    def test_sem_humano_a_entrada_chega_intacta(self):
        entrada = operacao(data="15/03/2027")
        entrada["itens"][0]["baseCalculo"] = "1.000,00"
        original = copy.deepcopy(entrada)
        saida = calc.executar_operacao(entrada, self.base, agora=AGORA)
        self.assertEqual(entrada, original)
        self.assertNotIn("normalizacoes", saida)
        self.assertEqual(saida["status"], 422)
        self.assertEqual(saida, {**saida, "status": simples_motor.calcular(
            copy.deepcopy(original), self.base, calc.BASE_URL_CLI, agora=AGORA)[0]})

    def test_com_humano_a_operacao_calcula_e_lista_os_avisos(self):
        entrada = operacao(data="15/03/2027")
        entrada["itens"][0]["baseCalculo"] = "1.000,00"
        saida = calc.executar_operacao(entrada, self.base, humano=True, agora=AGORA)
        esperado = simples_motor.calcular(operacao(), self.base, calc.BASE_URL_CLI, agora=AGORA)
        self.assertEqual(saida["status"], 200)
        self.assertEqual(saida["corpo"], esperado[1])
        self.assertEqual(len(saida["normalizacoes"]), 2)

    def test_humano_pela_linha_de_comando(self):
        entrada = operacao(data="15/03/2027")
        entrada["itens"][0]["baseCalculo"] = "1.000,00"
        processo = rodar_cli("--humano", entrada=json.dumps(entrada))
        self.assertEqual(processo.returncode, 0, processo.stderr)
        saida = json.loads(processo.stdout)
        self.assertEqual(saida["status"], 200)
        self.assertEqual(len(saida["normalizacoes"]), 2)


class NormalizarTest(unittest.TestCase):
    def test_normalizar_virgula_decimal(self):
        op, avisos = calc.normalizar({"aliquotasEfetivas": {"anexoI": {"pCBSSN": "2,3591"}},
                                      "itens": [{"baseCalculo": "1.000,00"}]})
        self.assertEqual(op["aliquotasEfetivas"]["anexoI"]["pCBSSN"], "2.3591")
        self.assertEqual(op["itens"][0]["baseCalculo"], "1000.00")
        self.assertEqual(len(avisos), 2)
        self.assertIn("aliquotasEfetivas.anexoI.pCBSSN", avisos[0])
        self.assertIn("2,3591", avisos[0])
        self.assertIn("2.3591", avisos[0])

    def corrigir(self, texto):
        op, avisos = calc.normalizar({"itens": [{"baseCalculo": texto}]})
        return op["itens"][0]["baseCalculo"], avisos

    def test_regra_do_ponto_e_da_virgula(self):
        casos_ok = {"1.000,00": "1000.00", "1,000.50": "1000.50", "1.234.567,89": "1234567.89",
                    "0,5": "0.5", "1,000": "1.000", "-2,5": "-2.5"}
        for entrada, saida in casos_ok.items():
            with self.subTest(entrada):
                self.assertEqual(self.corrigir(entrada)[0], saida)

    def test_ponto_sozinho_nunca_e_milhar(self):
        for texto in ("1.000", "1000.00", "1.5", "1.000.000"):
            with self.subTest(texto):
                valor, avisos = self.corrigir(texto)
                self.assertEqual(valor, texto)
                self.assertEqual(avisos, [])

    def test_ambiguo_ou_nao_numero_fica_como_esta_sem_aviso(self):
        for texto in ("1,2,3", "1.00,0.5", "abc", "1.0000,00", "12,", ",5", "1 000,00", "", "1,00 reais"):
            with self.subTest(texto):
                valor, avisos = self.corrigir(texto)
                self.assertEqual(valor, texto)
                self.assertEqual(avisos, [])

    def test_normalizar_numero_json_vira_texto_sem_perder_casa(self):
        op, avisos = calc.normalizar(json.loads(
            '{"tpDoc": 55, "itens": [{"numero": 1, "baseCalculo": 1000.00, "cAtivSN": 1,'
            ' "valorImpostoSeletivo": 0.10}]}', parse_float=Decimal))
        self.assertEqual(op["tpDoc"], "55")
        item = op["itens"][0]
        self.assertEqual(item["baseCalculo"], "1000.00")
        self.assertEqual(item["valorImpostoSeletivo"], "0.10")
        self.assertEqual(item["cAtivSN"], "1")  # sem zero a mais: completar seria mudar o conteúdo
        self.assertEqual(item["numero"], 1)
        self.assertEqual(len(avisos), 4)

    def test_normalizar_ncm_com_pontos(self):
        op, avisos = calc.normalizar({"itens": [{"ncm": "1006.30.21", "NBS": "1.0101.20.00", "nbs": "abc.12"}]})
        self.assertEqual(op["itens"][0]["ncm"], "10063021")
        self.assertEqual(op["itens"][0]["NBS"], "1010120" + "00")
        self.assertEqual(op["itens"][0]["nbs"], "abc.12")
        self.assertEqual(len(avisos), 2)

    def test_data_brasileira(self):
        op, avisos = calc.normalizar({"data": "15/03/2027"})
        self.assertEqual(op["data"], "2027-03-15")
        self.assertEqual(len(avisos), 1)
        for texto in ("2027-03-15", "15-03-2027", "15/3/2027", "03/15/27"):
            self.assertEqual(calc.normalizar({"data": texto})[0]["data"], texto)

    def test_nao_inventa_campo_nem_mexe_na_entrada(self):
        entrada = {"data": "15/03/2027", "itens": [{"baseCalculo": "1,5"}, 7, None]}
        copia = copy.deepcopy(entrada)
        op, _ = calc.normalizar(entrada)
        self.assertEqual(entrada, copia)
        self.assertEqual(set(op), {"data", "itens"})
        self.assertEqual(op["itens"][1:], [7, None])
        self.assertEqual(calc.normalizar([1, 2]), ([1, 2], []))
        self.assertEqual(calc.normalizar({}), ({}, []))

    def test_booleanos_e_aliquotas_com_ponto_ficam(self):
        entrada = {"aliquotasEfetivas": {"fatorR": True, "anexoIII": {"pCBSSN": "2.2154",
                   "semRedistribuicaoISS": {"pCBSSN": "1,99"}}}}
        op, avisos = calc.normalizar(entrada)
        self.assertIs(op["aliquotasEfetivas"]["fatorR"], True)
        self.assertEqual(op["aliquotasEfetivas"]["anexoIII"]["semRedistribuicaoISS"]["pCBSSN"], "1.99")
        self.assertEqual(len(avisos), 1)


class _Resposta:
    def __init__(self, texto):
        self.texto = texto

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, limite=-1):
        return self.texto.encode("utf-8")[:limite]


class ChecarVersaoTest(unittest.TestCase):
    META = {"calculadora": {"versao": "1.0.2"}}

    def setUp(self):
        self.original = calc._abrir
        self.addCleanup(setattr, calc, "_abrir", self.original)
        self.pedidos = []

    def trocar(self, resposta=None, erro=None):
        def falso(pedido, timeout=None):
            self.pedidos.append((pedido, timeout))
            if erro is not None:
                raise erro
            return _Resposta(resposta)
        calc._abrir = falso

    def test_checar_versao_sem_rede(self):
        self.trocar(erro=urllib.error.URLError("sem rede"))
        r = calc.checar_versao(self.META)
        self.assertEqual(r["consultado"], False)
        self.assertIsNone(r["ha_versao_nova"])
        self.assertTrue(r["mensagem"])

    def test_checar_versao_resposta_estranha(self):
        for texto in ("<html>oi</html>", "[]", '{"tag_name": 3}', '{"tag_name": "nightly"}', '{"x": 1}', ""):
            with self.subTest(texto):
                self.trocar(texto)
                self.assertFalse(calc.checar_versao(self.META)["consultado"])

    def test_checar_versao_demorada(self):
        for erro in (socket.timeout("lento"), TimeoutError(), ConnectionResetError(), ValueError("x")):
            with self.subTest(type(erro).__name__):
                self.trocar(erro=erro)
                self.assertFalse(calc.checar_versao(self.META)["consultado"])

    def test_checar_versao_igual_e_mais_nova(self):
        casos_versao = {"1.0.2": False, "v1.0.2": False, "1.0.1": False, "1.0.10": True, "1.1.0": True,
                        "2.0.0": True, "1.0": False, "0.9.9": False}
        for tag, nova in casos_versao.items():
            with self.subTest(tag):
                self.trocar(json.dumps({"tag_name": tag}))
                r = calc.checar_versao(self.META)
                self.assertTrue(r["consultado"])
                self.assertEqual(r["ha_versao_nova"], nova)
                self.assertEqual(r["calculadora"], tag)

    def test_versao_se_compara_por_numero_e_nao_por_texto(self):
        self.trocar(json.dumps({"tag_name": "1.0.10"}))
        self.assertTrue(calc.checar_versao({"calculadora": {"versao": "1.0.9"}})["ha_versao_nova"])

    def test_sem_versao_espelhada_ainda_informa_a_ultima(self):
        self.trocar(json.dumps({"tag_name": "1.0.2"}))
        r = calc.checar_versao({})
        self.assertTrue(r["consultado"])
        self.assertIsNone(r["ha_versao_nova"])

    def test_nenhum_numero_da_pessoa_vai_para_url(self):
        self.trocar(json.dumps({"tag_name": "1.0.2"}))
        calc.checar_versao(self.META, prazo=2.5)
        pedido, prazo = self.pedidos[0]
        self.assertEqual(pedido.full_url,
                         "https://api.github.com/repos/ReceitaFederal/calculadora-rtc-simples-nacional/releases/latest")
        self.assertNotIn("?", pedido.full_url)
        self.assertIsNone(pedido.data)
        self.assertEqual(pedido.get_header("User-agent"), "cd2-super-skill")
        self.assertEqual(prazo, 2.5)

    def test_checar_versao_pela_main_sai_0_mesmo_sem_rede(self):
        import contextlib
        import io
        self.trocar(erro=urllib.error.URLError("sem rede"))
        saida = io.StringIO()
        with contextlib.redirect_stdout(saida):
            codigo = calc.main(["--checar-versao"])
        self.assertEqual(codigo, 0)
        resposta = json.loads(saida.getvalue())
        self.assertEqual(set(resposta), {"consultado", "ha_versao_nova", "calculadora", "mensagem"})
        self.assertFalse(resposta["consultado"])


class BaseDanificadaTest(unittest.TestCase):
    PAGINA = 4096
    RAIZ_CLASSIFICACAO_TRIBUTARIA = 4  # página raiz da tabela, lida em sqlite_master

    def copia(self, pasta, dados):
        destino = Path(pasta) / "danificada.db"
        destino.write_bytes(dados)
        return destino

    def original(self):
        return (SKILL / "references" / "base-receita.db").read_bytes()

    def test_pagina_estragada_chega_como_base_indisponivel_na_consulta(self):
        from datetime import date
        dados = bytearray(self.original())
        inicio = (self.RAIZ_CLASSIFICACAO_TRIBUTARIA - 1) * self.PAGINA
        dados[inicio:inicio + self.PAGINA] = b"\xff" * self.PAGINA
        with tempfile.TemporaryDirectory() as pasta:
            with Base(self.copia(pasta, bytes(dados))) as base:  # a base abre: o dano só aparece na consulta
                with self.assertRaises(simples_base.BaseIndisponivel) as contexto:
                    base.classificacao("000001", date(2027, 1, 1))
        self.assertIn("danificada", str(contexto.exception))
        self.assertNotIsInstance(contexto.exception, sqlite3.Error)

    def test_base_cortada_em_60_por_cento_vira_base_indisponivel(self):
        from datetime import date
        dados = self.original()
        with tempfile.TemporaryDirectory() as pasta:
            caminho = self.copia(pasta, dados[: int(len(dados) * 0.6)])
            with self.assertRaises(simples_base.BaseIndisponivel):
                with Base(caminho) as base:
                    base.classificacao("000001", date(2027, 1, 1))

    def test_cli_com_base_danificada_sai_2_sem_traceback(self):
        dados = bytearray(self.original())
        inicio = (self.RAIZ_CLASSIFICACAO_TRIBUTARIA - 1) * self.PAGINA
        dados[inicio:inicio + self.PAGINA] = b"\xff" * self.PAGINA
        with tempfile.TemporaryDirectory() as pasta:
            scripts = Path(pasta) / "scripts"
            (Path(pasta) / "references").mkdir()
            scripts.mkdir()
            for nome in ("calc.py", "simples_motor.py", "simples_base.py", "numeros.py"):
                shutil.copy(SKILL / "scripts" / nome, scripts / nome)
            (Path(pasta) / "references" / "base-receita.db").write_bytes(bytes(dados))
            processo = rodar_cli("--atividades", "2027-01-01", "--cclasstrib", "000001", script=scripts / "calc.py")
        self.assertEqual(processo.returncode, 2)
        self.assertIn("errors", json.loads(processo.stdout))
        self.assertNotIn("Traceback", processo.stderr)

    def test_erro_de_sqlite_na_consulta_vira_base_indisponivel(self):
        with Base() as base:
            with self.assertRaises(simples_base.BaseIndisponivel):
                base._consultar("SELECT * FROM TABELA_QUE_NAO_EXISTE")


class FonteTest(unittest.TestCase):
    def test_sem_travessao_literal_nos_scripts_e_nos_testes(self):
        # texto copiado do Java com U+2014 ou U+2013 fica escrito com o escape
        arquivos = [SKILL / "scripts" / "simples_motor.py", SKILL / "scripts" / "simples_base.py",
                    SKILL / "scripts" / "calc.py",
                    Path(__file__)]
        for arquivo in arquivos:
            with self.subTest(arquivo.name):
                texto = arquivo.read_text(encoding="utf-8")
                self.assertNotIn("\u2014", texto)
                self.assertNotIn("\u2013", texto)


if __name__ == "__main__":
    unittest.main()
