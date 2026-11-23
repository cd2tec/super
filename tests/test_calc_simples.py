"""Testes do motor da skill cbs-ibs-simples (simples_motor.py), a parte da operação inteira.

Conferem o motor contra as respostas gravadas da calculadora oficial do Simples Nacional (tag 1.0.2,
base V0073): os casos do grupo `global` (validação da operação) e do grupo `erro` (catálogo VSN-).
Os 72 casos do grupo `item` são da Tarefa 6.

As respostas oficiais foram gravadas chamando a calculadora em http://127.0.0.1:8082/api. Por isso o
teste passa `base_url="http://127.0.0.1"` (esquema://host, sem porta: HttpUtils.getBaseURL), que entra
no `type` de cada erro. O `instance` ("/api/calculadora/observabilidade/simples-nacional/nao-assistido")
é o padrão de `calcular`. O `timestamp` do erro fica fora da conferência (config.json, `ignorar`).
"""
import copy
import importlib.util
import json
import sys
import unittest
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
                try:
                    status, _ = simples_motor.calcular(
                        caso["requisicao"]["corpo"], self.base, BASE_URL_OFICIAL, agora=AGORA)
                except NotImplementedError:
                    self.fail("o caso global chegou ao processamento dos itens")
                self.assertEqual(status, 422)

    def test_operacao_valida_para_no_ponto_dos_itens(self):
        with self.assertRaises(NotImplementedError) as contexto:
            self.calcular(operacao())
        self.assertEqual(str(contexto.exception), "itens: Tarefa 6")

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
                         "incoerente — quem excede o sublimite tem o ISS fora do Simples Nacional")


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
                with self.assertRaises(NotImplementedError):
                    self.calcular(operacao(data=texto))

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
        with self.assertRaises(NotImplementedError):
            self.calcular(operacao(CRT=" +1"))
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
        with self.assertRaises(NotImplementedError):
            self.calcular(operacao(CRT=1, tpDoc=55))

    def test_vsn_039_so_quando_o_regime_calcula(self):
        self.assertEqual([e[0] for e in self.erros(operacao(aliquotasEfetivas=None))], ["VSN-039"])
        # SOMENTE_RECEITA: a fonte nem é consultada
        with self.assertRaises(NotImplementedError):
            self.calcular(operacao(tpRegimeIBSCBS="1", aliquotasEfetivas=None))

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


if __name__ == "__main__":
    unittest.main()
