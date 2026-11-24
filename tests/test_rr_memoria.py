"""Testes da memória de cálculo (scripts/rr_memoria.py) fora da paridade com as respostas gravadas.

O caminho da quantidade em notação científica não tem resposta oficial na bateria da 1.7.2 (só os
modelos 620xxx usam [quantidade], e eles dão CAL-007): o port de BigDecimal.toString() é conferido aqui
contra as saídas conhecidas do Java.
"""
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
rr_memoria = load_module("rr_memoria", SKILL / "scripts" / "rr_memoria.py")
D = Decimal


class TextoDoBigDecimalTest(unittest.TestCase):
    """BigDecimal.toString(): as saídas do Java para cada forma do número (conferidas no JDK 25)."""

    CASOS = [
        (D("0.0000001"), "1E-7"),
        (D("1E+3"), "1E+3"),  # escala -3
        (D("123.4500"), "123.4500"),
        (D("0.000001"), "0.000001"),
        (D("0.0000010"), "0.0000010"),
        (D("1"), "1"),
        (D("0"), "0"),
        (D("-0"), "0"),  # BigDecimal não tem zero negativo
        (D("0.00"), "0.00"),
        (D("0E-8"), "0E-8"),
        (D("0E+2"), "0E+2"),
        (D("12.3E+5"), "1.23E+6"),
        (D("-1.5E-7"), "-1.5E-7"),
        (D("-0.5"), "-0.5"),
        (D("123456789.1234"), "123456789.1234"),
        (D("1234.5E-10"), "1.2345E-7"),
        (D("100"), "100"),
    ]

    def test_saidas_conhecidas_do_java(self):
        for valor, esperado in self.CASOS:
            self.assertEqual(rr_memoria.texto_big_decimal(valor), esperado, repr(valor))


class FormatoBrasileiroTest(unittest.TestCase):
    """DecimalFormat("#,##0.00") e ("#,##0.00##") com os símbolos de pt-BR e HALF_EVEN (saídas do JDK 25;
    negativo que arredonda a zero sai "-0,00", como no Java)."""

    def test_moeda(self):
        casos = {D("200"): "200,00", D("1234567.891"): "1.234.567,89", D("0.125"): "0,12", D("0.135"): "0,14",
                 D("0"): "0,00", None: "0,00", D("999.995"): "1.000,00", D("1E+3"): "1.000,00",
                 D("-1234.5"): "-1.234,50", D("-0.001"): "-0,00"}
        for valor, esperado in casos.items():
            self.assertEqual(rr_memoria.formatar_moeda(valor), esperado, repr(valor))

    def test_percentual(self):
        casos = {D("8.4"): "8,40", D("0.05"): "0,05", D("12.34565"): "12,3456", D("12.34575"): "12,3458",
                 D("0.1"): "0,10", D("60.0000"): "60,00", D("0.208"): "0,208", D("1000.5"): "1.000,50",
                 D("-0.00001"): "-0,00", None: "0,00"}
        for valor, esperado in casos.items():
            self.assertEqual(rr_memoria.formatar_percentual(valor), esperado, repr(valor))


class TokenizerTest(unittest.TestCase):
    """TemplateTokenizerService.tokenize e TokenizerService.substituirPlaceholders."""

    def test_substitui_e_esquece_chave_desconhecida(self):
        self.assertEqual(rr_memoria.substituir_placeholders("A [a] e [b].", {"a": "1"}), "A 1 e .")

    def test_sem_colchete_volta_igual(self):
        self.assertEqual(rr_memoria.substituir_placeholders("sem nada", {"a": "1"}), "sem nada")

    def test_colchete_sem_fechamento_fica_literal(self):
        self.assertEqual(rr_memoria.substituir_placeholders("x [a] y [b", {"a": "1"}), "x 1 y [b")

    def test_chave_com_colchete_de_abertura(self):
        self.assertEqual(rr_memoria.substituir_placeholders("[a[b]c]", {"a[b": "Z"}), "Zc]")

    def test_placeholders_colados(self):
        self.assertEqual(rr_memoria.substituir_placeholders("[a][a]", {"a": "1"}), "11")


if __name__ == "__main__":
    unittest.main()
