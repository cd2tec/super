"""Testes da base da Receita que a skill cbs-ibs-regime-regular leva dentro do pacote."""
import json
import sqlite3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import receita_base  # noqa: E402

PASTA = ROOT / "conferencia" / "cbs-ibs-regime-regular"
CONFIG = json.loads((PASTA / "config.json").read_text(encoding="utf-8"))
META = json.loads((PASTA / "meta.json").read_text(encoding="utf-8"))
BASE = ROOT / CONFIG["base_destino"]


class TestBaseDoRegimeRegular(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(f"file:{BASE}?mode=ro", uri=True)
        self.addCleanup(self.con.close)

    def test_base_bate_com_o_meta(self):
        self.assertEqual(receita_base.ler_meta(BASE), META)
        self.assertEqual(
            receita_base.hash_conteudo(self.con, CONFIG["tabelas"], CONFIG["hash_ignorar_colunas"]),
            META["base"]["hash"])
        self.assertRegex(META["base"]["versao"], r"^V\d{4}$")

    def test_so_tabelas_do_config_e_cd2_meta(self):
        nomes = {r[0] for r in self.con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        self.assertEqual(nomes, set(CONFIG["tabelas"]) | {"CD2_META"})

    def test_transferencia_cbs_tem_linha(self):
        # Sem linha nesta tabela o motor do regime regular devolve CAL-999.
        n = self.con.execute("SELECT COUNT(*) FROM TRANSFERENCIA_CBS_ENTE_GOV").fetchone()[0]
        self.assertGreater(n, 0)

    def test_base_menor_que_5_mb(self):
        self.assertLess(BASE.stat().st_size, 5 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
