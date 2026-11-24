"""Testes do cálculo da CBS e do IBS (scripts/rr_calculo.py) fora da paridade com as respostas gravadas."""
import importlib.util
import sys
import unittest
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "core" / "cd2-super" / "skills" / "cbs-ibs-regime-regular"


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


sys.path.insert(0, str(SKILL / "scripts"))
rr_calculo = load_module("rr_calculo", SKILL / "scripts" / "rr_calculo.py")
rr_erros = load_module("rr_erros", SKILL / "scripts" / "rr_erros.py")
D = Decimal


class AliquotaResultadoTest(unittest.TestCase):
    """O construtor do record AliquotaResultadoDTO e valorAplicavel()."""

    def _erro(self, *args):
        with self.assertRaises(rr_erros.ErroRR) as ctx:
            rr_calculo.aliquota_resultado(*args)
        return ctx.exception

    def test_formas_de_aplicacao(self):
        self.assertEqual(rr_calculo.aliquota_resultado(D("17.7"), None, None), D("17.7"))
        self.assertEqual(rr_calculo.aliquota_resultado(D("17.7"), D("1.3"), "ACRESCIMO"), D("19.0"))
        self.assertEqual(rr_calculo.aliquota_resultado(D("17.7"), D("1.3"), "DECRESCIMO"), D("16.4"))
        self.assertEqual(rr_calculo.aliquota_resultado(D("17.7"), D("12"), "SUBSTITUICAO"), D("12"))

    def test_referencia_obrigatoria_e_negativos(self):
        erro = self._erro(None, None, None)
        self.assertEqual((erro.codigo, erro.detalhe), ("CAL-003", "Alíquota de referência não encontrada"))
        self.assertEqual(self._erro(D("-1"), None, None).detalhe, "Alíquota de referência não pode ter valor negativo")
        self.assertEqual(self._erro(D("1"), D("-1"), "SUBSTITUICAO").detalhe,
                         "Alíquota padrão não pode ter valor negativo")
        erro = self._erro(D("1"), D("2"), "DECRESCIMO")
        self.assertEqual((erro.codigo, erro.detalhe),
                         ("CAL-005", "Alíquota aplicável resultante não pode ter valor negativo"))

    def test_forma_de_aplicacao_inconsistente(self):
        erro = self._erro(D("1"), D("2"), None)  # FormaAplicacaoNaoDefinidaException: sem CodigoErro
        self.assertEqual((erro.codigo, erro.titulo, erro.status_http, erro.estado_item),
                         (None, "Forma de Aplicação de Percentual não definida", 500, "FALHA_TECNICA"))
        self.assertEqual(self._erro(D("1"), D("2"), "OUTRA").titulo, "Erro de sistema não previsto")
        self.assertEqual(self._erro(D("1"), None, "ACRESCIMO").codigo, "CAL-002")


class SerializacaoTest(unittest.TestCase):
    def test_serializers_do_java(self):
        tributos = {"IBSCBS": {"CST": "000", "gIBSCBS": {"vBC": D("25"), "gCBS": {"pCBS": D("0.9000"),
                    "gRed": {"pRedAliq": D("60.00"), "pAliqEfet": D("0.3600")}, "vCBS": D("0.225")}}}}
        self.assertEqual(rr_calculo.para_json(tributos), {"IBSCBS": {"CST": "000", "gIBSCBS": {
            "vBC": "25.00", "gCBS": {"pCBS": "0.90", "gRed": {"pRedAliq": "60.00", "pAliqEfet": "0.36"},
                                     "vCBS": "0.22"}}}})
        self.assertEqual(rr_calculo.para_json({"qBCMono": D("12.50000")}), {"qBCMono": "12.5000"})
        with self.assertRaises(rr_calculo.EscritaFalhou):
            rr_calculo.para_json({"vCBS": D("-0.01")})

    def test_serializers_do_imposto_seletivo(self):
        dominio = {"CSTIS": "000", "cClassTribIS": "000001", "vBCIS": D("345.67"), "pIS": D("10.00"),
                   "pISEspec": D("1.2346"), "adRemIS": D("1.2346"), "uTrib": "LT", "qTrib": D("12.5"),
                   "vIS": D("49.9997")}
        self.assertEqual(rr_calculo.para_json({"IS": dominio}), {"IS": {
            "CSTIS": "000", "cClassTribIS": "000001", "vBCIS": "345.67", "pIS": "10.00", "pISEspec": "1.2346",
            "adRemIS": "1.2346", "uTrib": "LT", "qTrib": "12.5000", "vIS": "50.00"}})
        for chave, valor, mensagem in (("uTrib", "GARRAFA", "uTrib deve ter entre 1 e 6 caracteres"),
                                       ("uTrib", "", "uTrib deve ter entre 1 e 6 caracteres"),
                                       ("CSTIS", "0\u0660\u0660", "TCST deve ser uma string de exatamente 3 dígitos "
                                                                 "numéricos")):
            with self.assertRaises(rr_calculo.EscritaFalhou) as ctx:
                rr_calculo.para_json({"IS": dict(dominio, **{chave: valor})})
            self.assertEqual(str(ctx.exception), mensagem)

    def test_publicar_aliquota(self):
        self.assertEqual(rr_calculo._publicar_aliquota(D("0.0012345")), D("0.001234"))
        self.assertEqual(rr_calculo._expressao_usa_aliquota("aliquota * (1 - percentualReducao)"), True)
        self.assertEqual(rr_calculo._expressao_usa_aliquota("aliquotaEfetiva"), False)
        self.assertEqual(rr_calculo._expressao_usa_aliquota("2.08/100"), False)


if __name__ == "__main__":
    unittest.main()
