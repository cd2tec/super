"""Testes do avaliador de expressões do tratamento tributário (scripts/rr_expressao.py)."""
import importlib.util
import json
import sqlite3
import sys
import unittest
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "core" / "cd2-super" / "skills" / "cbs-ibs-regime-regular"
BASE_REDUZIDA = SKILL / "references" / "base-receita.db"


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


# Carga por spec, sem pôr a pasta de scripts no sys.path: o numeros.py entra primeiro em sys.modules,
# e o `import numeros` do rr_expressao.py acha ele lá.
base_completa_da_skill = load_module("versao_da_base", ROOT / "tests" / "versao_da_base.py").base_completa_da_skill
num = load_module("numeros", SKILL / "scripts" / "numeros.py")
rx = load_module("rr_expressao", SKILL / "scripts" / "rr_expressao.py")
D = Decimal


def expressoes(base):
    con = sqlite3.connect(f"file:{base}?mode=ro", uri=True)
    try:
        cols = [r[1] for r in con.execute("PRAGMA table_info(TRATAMENTO_TRIBUTARIO)")
                if r[1].startswith("TRTR_EXPRESSAO_")]
        achadas = set()
        for c in cols:
            achadas.update(r[0] for r in con.execute(
                f"SELECT DISTINCT {c} FROM TRATAMENTO_TRIBUTARIO WHERE {c} IS NOT NULL"))
        return sorted(achadas)
    finally:
        con.close()


class TestAvaliador(unittest.TestCase):
    def test_precedencia(self):
        self.assertEqual(rx.avaliar("1+2*3", {}), D(7))

    def test_parenteses(self):
        self.assertEqual(rx.avaliar("(1+2)*3", {}), D(9))

    def test_subtracao_associa_a_esquerda(self):
        self.assertEqual(rx.avaliar("10-3-2", {}), D(5))

    def test_divisao_usa_34_digitos(self):
        self.assertEqual(rx.avaliar("1/3", {}), num.dividir(D(1), D(3)))
        self.assertEqual(len(rx.avaliar("1/3", {}).as_tuple().digits), 34)

    def test_divisao_por_zero_levanta(self):
        with self.assertRaises(ZeroDivisionError):
            rx.avaliar("1/0", {})

    def test_variavel_sozinha_devolve_valor_sem_conta(self):
        v = D("0.123456789012345678901234567890123456789")
        self.assertIs(rx.avaliar(" aliquota ", {"aliquota": v}), v)

    def test_variavel_em_conta(self):
        self.assertEqual(rx.avaliar("baseCalculo*aliquota", {"baseCalculo": D("100.00"), "aliquota": D("0.1")}),
                         D("10.000"))

    def test_menos_unario(self):
        self.assertEqual(rx.avaliar("-2*-3", {}), D(6))
        self.assertEqual(rx.avaliar("1--1", {}), D(2))

    def test_soma_subtracao_multiplicacao_exatas(self):
        grande = D("1234567890123456789012345678901234567890")
        self.assertEqual(rx.avaliar("aliquota+1", {"aliquota": grande}), D(int(grande) + 1))
        self.assertEqual(rx.avaliar("aliquota*aliquota", {"aliquota": grande}), D(int(grande) ** 2))

    def test_escala_dos_literais_como_no_java(self):
        # Literal sem ponto vira inteiro (escala 0); com ponto vira double e depois BigDecimal do toString.
        self.assertEqual(str(rx.avaliar("1", {})), "1")
        self.assertEqual(str(rx.avaliar("1.00", {})), "1.0")
        self.assertEqual(str(rx.avaliar("0.5", {})), "0.5")

    def test_zero_nao_tem_sinal(self):
        self.assertEqual(str(rx.avaliar("0*-1", {})), "0")

    def test_tokens_proibidos(self):
        for ruim in ["__import__('os')", "__import__", "a.b", "1; 2", "2**3", "1 and 2", "'a'", "", "1+", "x", "(1", "1)"]:
            with self.subTest(ruim=ruim), self.assertRaises(ValueError):
                rx.avaliar(ruim, {"aliquota": D(1)})

    def test_variavel_sem_valor_levanta(self):
        with self.assertRaises(ValueError):
            rx.avaliar("aliquota+1", {})

    def avaliar_todas(self, base):
        # Com 1, (1-pBio) vira zero e a divisão falha, igual ao Java; 0.5 evita isso.
        variaveis = {n: D("0.5") for n in rx.VARIAVEIS}
        self.assertEqual(len(variaveis), 21)
        exprs = expressoes(base)
        self.assertGreater(len(exprs), 5, base)
        # Quem chama (CalculoCbsIbsService) só avalia expressão não vazia (isNotBlank).
        for e in (e for e in exprs if e.strip()):
            with self.subTest(base=base.name, expressao=e):
                self.assertIsInstance(rx.avaliar(e, variaveis), Decimal)

    def test_todas_as_expressoes_da_base_avaliam_com_variaveis_0_5(self):
        self.avaliar_todas(BASE_REDUZIDA)

    def test_todas_as_expressoes_da_base_completa_avaliam(self):
        completa = base_completa_da_skill("cbs-ibs-regime-regular")
        if completa is None:
            self.skipTest("sem CD2_BASE_COMPLETA da cbs-ibs-regime-regular")
        self.avaliar_todas(completa)


if __name__ == "__main__":
    unittest.main()
