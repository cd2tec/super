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
from unittest import mock
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
        self.assertEqual(saida["versao"], {"calculadora": "1.0.2", "base": "V0073", "conferida_em": "2026-11-22"})
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
        for texto in ("1000.00", "1.5", "1.000.000", "1.0000"):
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


class NormalizarRevisaoTest(unittest.TestCase):
    def avisos_de(self, texto, campo="baseCalculo"):
        op, avisos = calc.normalizar({"itens": [{campo: texto}]})
        return op["itens"][0][campo], avisos

    def test_ponto_com_tres_digitos_avisa_sem_mudar_o_valor(self):
        for texto in ("1.000", "12.500"):
            with self.subTest(texto):
                valor, avisos = self.avisos_de(texto)
                self.assertEqual(valor, texto)
                self.assertEqual(avisos, [f'Em itens[0].baseCalculo, "{texto}" foi lido como '
                                         f'{texto.replace(".", ",")}, e não como milhar. '
                                         "Se era milhar, escreva sem o ponto."])

    def test_virgula_com_tres_digitos_muda_e_avisa_do_milhar(self):
        valor, avisos = self.avisos_de("1,000")
        self.assertEqual(valor, "1.000")
        self.assertEqual(len(avisos), 2)
        self.assertIn('troquei "1,000" por "1.000"', avisos[0])
        self.assertEqual(avisos[1], 'Em itens[0].baseCalculo, "1,000" foi lido como 1,000, e não como milhar. '
                                    "Se era milhar, escreva sem o ponto.")

    def test_outras_formas_de_ponto_nao_avisam(self):
        for texto in ("1.0000", "1.00", "1.5", "1.000.000", "0.022", "0.001"):
            with self.subTest(texto):
                self.assertEqual(self.avisos_de(texto), (texto, []))

    def test_o_aviso_do_milhar_vale_para_aliquota_tambem(self):
        op, avisos = calc.normalizar({"aliquotasEfetivas": {"anexoI": {"pCBSSN": "1.000"}}})
        self.assertEqual(op["aliquotasEfetivas"]["anexoI"]["pCBSSN"], "1.000")
        self.assertEqual(len(avisos), 1)
        self.assertIn("aliquotasEfetivas.anexoI.pCBSSN", avisos[0])

    def test_expoente_enorme_fica_para_o_motor(self):
        import time
        inicio = time.monotonic()
        op, avisos = calc.normalizar(json.loads(
            '{"itens": [{"baseCalculo": 1E+999999999, "valorImpostoSeletivo": 1E-999999999}]}',
            parse_float=Decimal))
        self.assertLess(time.monotonic() - inicio, 1)
        self.assertIsInstance(op["itens"][0]["baseCalculo"], Decimal)
        self.assertIsInstance(op["itens"][0]["valorImpostoSeletivo"], Decimal)
        self.assertEqual(avisos, [])

    def test_espacos_nas_pontas_do_numero_saem_com_aviso(self):
        valor, avisos = self.avisos_de("  1500.00 \t")
        self.assertEqual(valor, "1500.00")
        self.assertEqual(len(avisos), 1)
        valor, avisos = self.avisos_de(" 1.500,00 ")
        self.assertEqual(valor, "1500.00")
        self.assertEqual(len(avisos), 1)
        self.assertEqual(self.avisos_de(" abc "), (" abc ", []))

    def test_ncm_so_perde_separador_entre_grupos_de_digitos(self):
        casos_ncm = {"1006.30.21": "10063021", "1006 30 21": "10063021", "1006-30-21": "10063021",
                     "+1006.30.21": "+1006.30.21", "1006.30.2x": "1006.30.2x", ".10063021": ".10063021",
                     "10063021.": "10063021.", " 10063021": " 10063021", "1006..30": "1006..30",
                     "10063021": "10063021"}
        for entrada, saida in casos_ncm.items():
            with self.subTest(entrada):
                self.assertEqual(self.avisos_de(entrada, "ncm")[0], saida)

    def test_numero_com_grupos_mal_formados_fica_como_esta(self):
        for texto in ("0.001,5", "0,001.5", "01.000,00", "1.00,5", "1.0000,5", "10.00.000,5"):
            with self.subTest(texto):
                self.assertEqual(self.avisos_de(texto), (texto, []))

    def test_aviso_de_numero_que_virou_texto(self):
        op, avisos = calc.normalizar(json.loads('{"tpDoc": 55, "itens": [{"baseCalculo": 10.50}]}',
                                                parse_float=Decimal))
        self.assertEqual(avisos, ['Em tpDoc, o número 55 virou o texto "55".',
                                  'Em itens[0].baseCalculo, o número 10.50 virou o texto "10.50".'])
        _, avisos = calc.normalizar({"tpDoc": Decimal("1E+3")})
        self.assertEqual(avisos, ['tpDoc: troquei "1E+3" por "1000" (número do JSON virou texto).'])


class ModosExclusivosTest(unittest.TestCase):
    def test_combinacoes_de_modos_sao_erro_de_uso(self):
        combinacoes = [("--erro", "VSN-001", "--versao"), ("--versao", "--checar-versao"),
                       ("--atividades", "2027-01-01", "--erro", "VSN-001"),
                       ("--atividade", "01", "--data", "2027-01-01", "--versao"),
                       ("--input", "x.json", "--versao"), ("--input", "x.json", "--atividades", "2027-01-01")]
        for args in combinacoes:
            with self.subTest(args):
                processo = rodar_cli(*args)
                self.assertEqual(processo.returncode, 2)
                self.assertEqual(processo.stdout, "")
                self.assertNotIn("Traceback", processo.stderr)

    def test_humano_com_consulta_e_erro_de_uso(self):
        for args in (("--humano", "--erro", "VSN-001"), ("--humano", "--versao"),
                     ("--humano", "--atividades", "2027-01-01"), ("--humano", "--checar-versao")):
            with self.subTest(args):
                processo = rodar_cli(*args)
                self.assertEqual(processo.returncode, 2)
                self.assertNotIn("Traceback", processo.stderr)

    def test_data_so_com_atividade(self):
        self.assertEqual(rodar_cli("--data", "2027-01-01", "--versao").returncode, 2)
        self.assertEqual(rodar_cli("--atividade", "01", "--data", "2027-01-01").returncode, 0)


class ChecarVersaoRevisaoTest(unittest.TestCase):
    META = {"calculadora": {"versao": "1.0.2"}}

    def setUp(self):
        self.original = calc._abrir
        self.addCleanup(setattr, calc, "_abrir", self.original)

    def test_tag_com_sufixo_compara_pela_parte_numerica(self):
        for tag, nova in {"1.0.3-rc1": True, "v1.0.3+build": True, "v1.0.2-rc1": False, "1.0.2+x": False}.items():
            with self.subTest(tag):
                calc._abrir = lambda pedido, timeout=None, tag=tag: _Resposta(json.dumps({"tag_name": tag}))
                r = calc.checar_versao(self.META)
                self.assertTrue(r["consultado"])
                self.assertEqual(r["ha_versao_nova"], nova)

    def test_qualquer_falha_ao_ler_a_meta_sai_0(self):
        class BaseQuebrada:
            def __init__(self, *_):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def meta(self):
                raise KeyError("calculadora")

        class BaseQueNaoAbre:
            def __init__(self, *_):
                raise json.JSONDecodeError("ruim", "", 0)

        import contextlib
        import io
        calc._abrir = lambda pedido, timeout=None: _Resposta(json.dumps({"tag_name": "1.0.2"}))
        original = simples_base.Base
        self.addCleanup(setattr, simples_base, "Base", original)
        for falsa in (BaseQuebrada, BaseQueNaoAbre):
            with self.subTest(falsa.__name__):
                simples_base.Base = falsa
                saida = io.StringIO()
                with contextlib.redirect_stdout(saida):
                    codigo = calc.main(["--checar-versao"])
                self.assertEqual(codigo, 0)
                self.assertTrue(json.loads(saida.getvalue())["consultado"])

    def test_versao_resumida_com_meta_incompleta(self):
        class BaseSemMeta:
            def __init__(self, meta):
                self._meta = meta

            def meta(self):
                return self._meta

            def versao_base(self):
                return {"versao": "V0073"}

        for meta in ({}, {"calculadora": None, "conferencia": []}, []):
            with self.subTest(meta=meta):
                self.assertEqual(calc._versao_resumida(BaseSemMeta(meta)),
                                 {"calculadora": "", "base": "V0073", "conferida_em": ""})


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


class FalhasInesperadasTest(ComBase):
    """O Java captura qualquer Exception no cálculo do item e qualquer RuntimeException na nomenclatura."""

    def setUp(self):
        import contextlib
        import io
        pilha = contextlib.ExitStack()
        pilha.enter_context(contextlib.redirect_stderr(io.StringIO()))  # a linha de log da falha injetada
        self.addCleanup(pilha.close)

    def item_com_erro(self, corpo):
        objeto = corpo["objetos"][0]
        self.assertEqual(objeto["estadoItem"], "FALHA_TECNICA")
        self.assertEqual(len(objeto["erros"]), 1)
        return objeto["erros"][0]

    def test_falha_qualquer_no_calculo_do_item_vira_vsn_999_com_a_mensagem(self):
        for excecao in (RuntimeError("boom"), ZeroDivisionError("divisao"), KeyError("chave"), ValueError("v")):
            with self.subTest(type(excecao).__name__):
                with mock.patch.object(simples_motor, "_calcular_item", side_effect=excecao):
                    status, corpo = self.calcular(operacao())
                self.assertEqual(status, 200)
                erro = self.item_com_erro(corpo)
                self.assertEqual((erro["code"], erro["pointer"]), ("VSN-999", "#/itens/0"))
                self.assertEqual(erro["detail"], str(excecao))

    def test_falha_inesperada_escreve_uma_linha_na_saida_de_erro_e_nao_muda_o_json(self):
        import contextlib
        import io
        with mock.patch.object(simples_motor, "_calcular_item", side_effect=RuntimeError("boom")):
            with contextlib.redirect_stderr(io.StringIO()) as erro:
                _, corpo = self.calcular(operacao())
        linhas = erro.getvalue().splitlines()
        self.assertEqual(len(linhas), 1)
        self.assertIn("falha t\u00e9cnica no item 0: RuntimeError: boom", linhas[0])
        self.assertEqual(self.item_com_erro(corpo)["detail"], "boom")  # o JSON segue o do Java
        with contextlib.redirect_stderr(io.StringIO()) as erro:
            self.calcular(operacao())
        self.assertEqual(erro.getvalue(), "")

    def test_excecao_sem_mensagem_sai_sem_detalhe(self):
        with mock.patch.object(simples_motor, "_calcular_item", side_effect=RuntimeError()):
            _, corpo = self.calcular(operacao())
        self.assertNotIn("detail", self.item_com_erro(corpo))

    def test_a_falha_de_um_item_nao_derruba_os_outros(self):
        original = simples_motor._calcular_item
        chamadas = []

        def falha_na_primeira(*args):
            chamadas.append(1)
            if len(chamadas) == 1:
                raise RuntimeError("so a primeira")
            return original(*args)
        with mock.patch.object(simples_motor, "_calcular_item", side_effect=falha_na_primeira):
            _, corpo = self.calcular(operacao(itens=[dict(ITEM), dict(ITEM, numero=2)]))
        self.assertEqual([o["estadoItem"] for o in corpo["objetos"]], ["FALHA_TECNICA", "CALCULADO"])

    def test_falha_na_validacao_estrutural_da_nomenclatura_vira_vsn_999(self):
        class BaseQueFalha(Base):
            def existe_ncm(self, ncm, data):
                raise RuntimeError("falha ao consultar a NCM")
        base = BaseQueFalha()
        self.addCleanup(base.fechar)
        corpo = operacao(itens=[dict(ITEM, ncm="12345678"), dict(ITEM, numero=2)])
        status, saida = simples_motor.calcular(corpo, base, BASE_URL_OFICIAL, agora=AGORA)
        self.assertEqual(status, 200)
        erro = saida["objetos"][0]["erros"][0]
        self.assertEqual((erro["code"], erro["pointer"], erro["detail"]),
                         ("VSN-999", "#/itens/0", "falha ao consultar a NCM"))
        self.assertEqual(saida["objetos"][0]["estadoItem"], "FALHA_TECNICA")
        self.assertEqual(saida["objetos"][1]["estadoItem"], "CALCULADO")

    def test_falha_na_validacao_pela_classificacao_vira_vsn_999(self):
        class BaseQueFalha(Base):
            def validar_nomenclatura(self, *args, **kwargs):
                raise ZeroDivisionError("conta")
        base = BaseQueFalha()
        self.addCleanup(base.fechar)
        status, saida = simples_motor.calcular(operacao(), base, BASE_URL_OFICIAL, agora=AGORA)
        self.assertEqual(status, 200)
        erro = saida["objetos"][0]["erros"][0]
        self.assertEqual((erro["code"], erro["detail"]), ("VSN-999", "conta"))

    def test_base_danificada_nao_vira_vsn_999(self):
        class BaseDanificada(Base):
            def existe_ncm(self, ncm, data):
                raise simples_base.BaseIndisponivel("arquivo cortado")
        base = BaseDanificada()
        self.addCleanup(base.fechar)
        with self.assertRaises(simples_base.BaseIndisponivel):
            simples_motor.calcular(operacao(itens=[dict(ITEM, ncm="12345678")]), base, BASE_URL_OFICIAL, agora=AGORA)

    def test_criticas_conhecidas_continuam_com_o_codigo_proprio(self):
        _, corpo = self.calcular(operacao(itens=[dict(ITEM, ncm="12345678", nbs="123456789")]))
        self.assertEqual(corpo["objetos"][0]["erros"][0]["code"], "VSN-041")


class DefeitosDaBateriaCompletaTest(ComBase):
    """Cada defeito que a bateria completa achou na primeira rodada, com o que a oficial responde."""

    def aliquotas(self, valor):
        return operacao(aliquotasEfetivas=valor)

    def detalhe(self, corpo):
        status, saida = self.calcular(corpo)
        self.assertEqual(status, 400)
        return saida["detail"]

    def test_texto_no_lugar_de_registro_e_aparado_e_vazio_e_coercao_recusada(self):
        tipo = "br.gov.serpro.rtc.api.simplesnacional.model.input.AliquotasEfetivasInput"
        vazio = f'JSON parse error: Cannot coerce empty String ("") to `{tipo}` value (but could if coercion was enabled using `CoercionConfig`)'
        for texto in ("", " ", " \t "):
            self.assertEqual(self.detalhe(self.aliquotas(texto)), vazio)
        self.assertEqual(
            self.detalhe(self.aliquotas(" 1 ")),
            f"JSON parse error: Cannot construct instance of `{tipo}` (although at least one Creator exists): "
            "no String-argument constructor/factory method to deserialize from String value ('1')")

    def test_numero_no_lugar_de_registro_diz_int_long_ou_biginteger_e_o_double_do_java(self):
        tipo = "br.gov.serpro.rtc.api.simplesnacional.model.input.AliquotasEfetivasInput"
        prefixo = f"JSON parse error: Cannot construct instance of `{tipo}` (although at least one Creator exists): no "
        casos = [(7, "int/Int", "7"), (-2 ** 31 - 1, "long/Long", str(-2 ** 31 - 1)),
                 (2 ** 40, "long/Long", str(2 ** 40)), (2 ** 70, "BigInteger", str(2 ** 70)),
                 (Decimal("1.5"), "double/Double", "1.5"), (Decimal("1E+3"), "double/Double", "1000.0"),
                 (Decimal("1E+30"), "double/Double", "1.0E30"), (Decimal("0.000001"), "double/Double", "1.0E-6"),
                 (Decimal("0.001"), "double/Double", "0.001"), (Decimal("12345678.9"), "double/Double", "1.23456789E7"),
                 (Decimal("-0.0"), "double/Double", "-0.0"), (Decimal("100.250"), "double/Double", "100.25")]
        for valor, construtor, mostrado in casos:
            with self.subTest(valor=valor):
                detalhe = self.detalhe(self.aliquotas(valor))
                self.assertTrue(detalhe.startswith(prefixo + construtor), detalhe)
                self.assertTrue(detalhe.endswith(f"from Number value ({mostrado})"), detalhe)

    def test_booleano_vazio_ou_em_branco_vale_nulo(self):
        for campo in ("fatorR", "impedimentoSublimite"):
            for texto in ("", " ", " \t "):
                with self.subTest(campo=campo, texto=texto):
                    linha = copy.deepcopy(LINHA)
                    linha[campo] = texto
                    esperado = copy.deepcopy(LINHA)
                    esperado[campo] = None
                    self.assertEqual(self.calcular(operacao(aliquotasEfetivas=linha)),
                                     self.calcular(operacao(aliquotasEfetivas=esperado)))

    def test_um_parametro_anexos_e_partido_nas_virgulas_e_aparado_o_repetido_nao(self):
        so_um = self.base.atividades("2027-01-01", "IV, V")
        igual = self.base.atividades("2027-01-01", ["IV", "V"])
        self.assertEqual(so_um, igual)
        self.assertEqual(self.base.atividades("2027-01-01", " III ")[0], 200)
        self.assertEqual(self.base.atividades("2027-01-01", "I,,V,")[1], self.base.atividades("2027-01-01", ["I", "V"])[1])
        status, corpo = self.base.atividades("2027-01-01", ["I", " V"])
        self.assertEqual(status, 400)
        self.assertEqual(corpo["detail"], "Anexo inv\u00e1lido: ' V'. Valores aceitos: I, II, III, IV, V.")
        self.assertEqual(self.base.atividades("2027-01-01", "")[0], 200)

    def test_texto_null_vale_nulo_no_booleano_e_a_mensagem_sai_aparada(self):
        linha = copy.deepcopy(LINHA)
        linha["fatorR"] = " null "
        esperado = copy.deepcopy(LINHA)
        esperado["fatorR"] = None
        self.assertEqual(self.calcular(operacao(aliquotasEfetivas=linha)), self.calcular(operacao(aliquotasEfetivas=esperado)))
        linha["fatorR"] = " Null "  # só "null" minúsculo vale nulo
        self.assertEqual(self.detalhe(operacao(aliquotasEfetivas=linha)),
                         'JSON parse error: Cannot deserialize value of type `java.lang.Boolean` from String "Null": '
                         'only "true" or "false" recognized')

    def test_double_do_java_nos_extremos(self):
        tipo = "br.gov.serpro.rtc.api.simplesnacional.model.input.AliquotasEfetivasInput"
        for valor, mostrado in ((Decimal("1E+400"), "Infinity"), (Decimal("-1E+400"), "-Infinity"),
                                (Decimal("1E-400"), "0.0"), (Decimal("4.9E-324"), "4.9E-324"),
                                (Decimal("1.7976931348623157E+308"), "1.7976931348623157E308"),
                                (Decimal("0.1234567890123456789"), "0.12345678901234568"),
                                (Decimal("123456789012345678901234567890.5"), "1.2345678901234568E29"),
                                (Decimal("12345678901234567.0"), "1.2345678901234568E16"),
                                (Decimal("9999999.999"), "9999999.999"), (Decimal("10000000.5"), "1.00000005E7"),
                                (Decimal("0.00099"), "9.9E-4")):
            with self.subTest(valor=valor):
                self.assertTrue(self.detalhe(self.aliquotas(valor)).endswith(f"from Number value ({mostrado})"))

    def test_ano_da_data_e_o_da_era_na_operacao_e_nas_consultas(self):
        status, corpo = self.calcular(operacao(data="-2027-03-15"))  # o yyyy do Java: 1 - (-2027) = 2028
        self.assertEqual((status, corpo["oper"]["data"]), (200, "-2027-03-15"))
        self.assertEqual(self.calcular(operacao(data="-2028-02-29"))[0], 200)  # bissexto, e 2029 não é
        for data in ("-9999-03-15", "-12027-03-15", "+10000-03-15"):  # ano da era acima de 9999: falha da oficial
            self.assertEqual(self.calcular(operacao(data=data))[0], 500, data)
        status, corpo = self.calcular(operacao(data="-0000-03-15"))  # menos zero não existe
        self.assertEqual((status, corpo["errors"][0]["code"]), (422, "VSN-012"))
        for data in ("0000-03-15", "-0027-03-15", "-00001-03-15"):  # existem, mas sem vigência
            status, corpo = self.calcular(operacao(data=data))
            self.assertEqual((status, corpo["errors"][0]["code"]), (422, "VSN-015"), data)
        self.assertEqual(self.base.atividades("-2027-01-01")[0], 200)
        self.assertEqual(len(self.base.atividades("-2027-01-01")[1]), 15)
        self.assertEqual(self.base.atividades("-0004-02-29"), (200, []))
        self.assertEqual(self.base.atividades("0000-01-01"), (200, []))
        self.assertEqual(self.base.atividade("01", "-2027-01-01")[0], 200)
        self.assertEqual(self.base.atividade("01", "-2027-01-01+01:00")[0], 200)
        self.assertEqual(self.base.atividades("2027-01-01z")[0], 200)
        for data in ("+10000-01-01", "-999999999-01-01"):
            status, corpo = self.base.atividades(data, base_url=BASE_URL_OFICIAL, agora=AGORA)
            self.assertEqual((status, corpo["detail"], corpo["title"]),
                             (500, "Index -1 out of bounds for length 4", "Erro de sistema n\u00e3o previsto"))
        for data in ("-0000-01-01", "\uff19999-12-31", "+9999-01-01", "-1000000000-01-01", "12027-01-01"):
            self.assertEqual(self.base.atividades(data)[0], 400, data)

    def test_consulta_de_erro_sem_o_parametro_codigo_e_400(self):
        status, corpo = simples_motor.consultar_erro(None, base_url=BASE_URL_OFICIAL, agora=AGORA)
        self.assertEqual(status, 400)
        self.assertEqual(corpo["detail"], "O par\u00e2metro obrigat\u00f3rio 'codigo' (String) n\u00e3o foi informado.")
        self.assertEqual(corpo["instance"], "/api/calculadora/observabilidade/simples-nacional/erros")
        self.assertEqual(simples_motor.consultar_erro("VSN-001"), simples_motor.descrever_erro("VSN-001"))
        self.assertEqual(simples_motor.consultar_erro(""), (404, None))

    def test_versao_traz_o_arquivo_e_o_ambiente_da_instalacao_oficial_quando_o_meta_os_tem(self):
        corpo = calc._executar_versao(self.base)["corpo"]
        self.assertEqual(list(corpo), ["versaoApp", "versaoDb", "descricaoVersaoDb", "dataVersaoDb", "arquivoDb",
                                       "ambiente"])
        self.assertEqual((corpo["arquivoDb"], corpo["ambiente"]), ("calculadora-pro.db", "offline"))


class FonteTest(unittest.TestCase):
    def test_sem_travessao_literal_nos_scripts_e_nos_testes(self):
        # texto copiado do Java com U+2014 ou U+2013 fica escrito com o escape
        arquivos = [SKILL / "scripts" / "simples_motor.py", SKILL / "scripts" / "simples_base.py",
                    SKILL / "scripts" / "calc.py",
                    Path(__file__), ROOT / "tests" / "test_simples_cobertura.py",
                    ROOT / "tests" / "test_receita_paridade.py", PASTA_CONFERENCIA / "bateria.py",
                    ROOT / "scripts" / "receita_paridade.py"]
        for arquivo in arquivos:
            with self.subTest(arquivo.name):
                texto = arquivo.read_text(encoding="utf-8")
                self.assertNotIn("\u2014", texto)
                self.assertNotIn("\u2013", texto)


if __name__ == "__main__":
    unittest.main()
