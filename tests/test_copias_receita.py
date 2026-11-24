"""Testes das cópias de arquivos entre a skill cbs-ibs-simples e a cbs-ibs-regime-regular.

Cada par em COPIAS_IGUAIS é um arquivo copiado do Simples para o RR e tem que ficar
byte a byte igual (comparado por sha256). Quem divergir de propósito entra em
COPIAS_DIVERGENTES com o motivo escrito.
"""
import hashlib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_SIMPLES = ROOT / "core" / "cd2-super" / "skills" / "cbs-ibs-simples" / "scripts"
SCRIPTS_RR = ROOT / "core" / "cd2-super" / "skills" / "cbs-ibs-regime-regular" / "scripts"

# (arquivo no Simples, arquivo no RR): precisam ser byte a byte iguais
COPIAS_IGUAIS = [("numeros.py", "numeros.py")]

# (arquivo no Simples, arquivo no RR, motivo): divergência intencional
COPIAS_DIVERGENTES = [
    ("sugestao.py", "sugestao.py",
     "A sugestão do RR lê a base pelo rr_base.py (buscar_classificacao_tributaria, buscar_percentual_reducao, "
     "alíquotas de IS do motor) em vez do simples_base.py, e perde o que é só do Simples (_efeito, _tp_rbsn, "
     "efeito_no_simples, efeito_texto e a frase de que a redução não tem efeito): no regime regular a redução "
     "sugerida vale na conta."),
]


def _sha256(caminho):
    return hashlib.sha256(caminho.read_bytes()).hexdigest()


class TestCopiasIguais(unittest.TestCase):
    def test_copias_iguais(self):
        for nome_simples, nome_rr in COPIAS_IGUAIS:
            with self.subTest(par=(nome_simples, nome_rr)):
                origem = SCRIPTS_SIMPLES / nome_simples
                destino = SCRIPTS_RR / nome_rr
                self.assertTrue(destino.is_file(), f"cópia ausente: {destino}")
                self.assertEqual(
                    _sha256(origem),
                    _sha256(destino),
                    f"{nome_rr} do RR diverge de {nome_simples} do Simples",
                )

    def test_divergentes_tem_motivo(self):
        for entrada in COPIAS_DIVERGENTES:
            with self.subTest(par=entrada[:2]):
                nome_simples, nome_rr, motivo = entrada
                self.assertTrue(
                    isinstance(motivo, str) and motivo.strip(),
                    f"divergência {nome_simples} -> {nome_rr} sem motivo",
                )


if __name__ == "__main__":
    unittest.main()
