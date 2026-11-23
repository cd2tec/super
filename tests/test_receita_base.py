"""Testes do montador da base da Receita (scripts/receita_base.py)."""
import importlib.util
import json
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "receita_base"
SKILL = "cbs-ibs-simples"
CONFIG = json.loads((ROOT / "conferencia" / SKILL / "config.json").read_text(encoding="utf-8"))


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


receita_base = load_module("receita_base", ROOT / "scripts" / "receita_base.py")


def ordem(con):
    return [r[0] for r in con.execute("SELECT ARQUIVO FROM ORDEM ORDER BY ID")]


class CopiaDaFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="receita_base_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.sql = self.tmp / "sql"
        shutil.copytree(FIXTURE, self.sql)


class TestMontar(CopiaDaFixture):
    def test_ordem_numerica_e_nao_alfabetica(self):
        # V0010 roda depois de V0002; V9 (alfabeticamente depois de V0010) roda entre os dois.
        con = receita_base.montar(self.sql)
        self.assertEqual(ordem(con), ["beforeMigrate", "B0001__x", "V0002__b", "V0010__c", "afterMigrate"])
        (self.sql / "manutencao" / "V9__d.sql").write_text(
            "INSERT INTO ORDEM (ARQUIVO) VALUES ('V9__d');\n", encoding="utf-8")
        con = receita_base.montar(self.sql)
        self.assertEqual(
            ordem(con), ["beforeMigrate", "B0001__x", "V0002__b", "V9__d", "V0010__c", "afterMigrate"])

    def test_migracao_que_falha_diz_o_arquivo(self):
        (self.sql / "manutencao" / "V0011__ruim.sql").write_text(
            "INSERT INTO NAO_EXISTE VALUES (1);\n", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, r"V0011__ruim\.sql"):
            receita_base.montar(self.sql)

    def test_migracao_que_falha_desfaz_o_que_fez(self):
        (self.sql / "manutencao" / "V0011__ruim.sql").write_text(
            "INSERT INTO PAI VALUES (9, 'nove');\nINSERT INTO NAO_EXISTE VALUES (1);\n", encoding="utf-8")
        with self.assertRaises(RuntimeError):
            receita_base.montar(self.sql)

    def test_migracao_aplicada(self):
        con = receita_base.montar(self.sql)
        self.assertEqual(con.execute("SELECT PAI_NOME FROM PAI WHERE PAI_ID = 3").fetchone()[0], "TRES")

    def test_pasta_sem_arquivo_obrigatorio_diz_qual(self):
        (self.sql / "criacao" / "afterMigrate.sql").unlink()
        with self.assertRaisesRegex(RuntimeError, r"afterMigrate\.sql"):
            receita_base.montar(self.sql)


class TestReduzirEHash(CopiaDaFixture):
    def setUp(self):
        super().setUp()
        self.con = receita_base.montar(self.sql)

    def test_reduzir_leva_so_as_tabelas_pedidas(self):
        destino = self.tmp / "reduzida.db"
        receita_base.reduzir(self.con, ["PAI", "FILHO"], destino)
        out = sqlite3.connect(destino)
        nomes = {r[0] for r in out.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        self.assertEqual(nomes, {"PAI", "FILHO"})
        self.assertEqual(out.execute("SELECT COUNT(*) FROM PAI").fetchone()[0], 3)
        self.assertEqual(
            receita_base.hash_conteudo(out, ["PAI", "FILHO"]),
            receita_base.hash_conteudo(self.con, ["PAI", "FILHO"]))
        out.close()

    def test_reduzir_tabela_que_nao_existe_falha(self):
        with self.assertRaisesRegex(RuntimeError, "NAO_EXISTE"):
            receita_base.reduzir(self.con, ["PAI", "NAO_EXISTE"], self.tmp / "x.db")

    def test_hash_formato(self):
        h = receita_base.hash_conteudo(self.con, ["PAI"])
        self.assertRegex(h, r"^sha256:[0-9a-f]{64}$")

    def test_hash_nao_depende_da_ordem_de_insercao(self):
        a = sqlite3.connect(":memory:")
        b = sqlite3.connect(":memory:")
        for con in (a, b):
            con.execute("CREATE TABLE T (X INTEGER, Y TEXT)")
        linhas = [(1, "a"), (2, None), (3, "c")]
        a.executemany("INSERT INTO T VALUES (?, ?)", linhas)
        b.executemany("INSERT INTO T VALUES (?, ?)", reversed(linhas))
        self.assertEqual(receita_base.hash_conteudo(a, ["T"]), receita_base.hash_conteudo(b, ["T"]))

    def test_hash_nao_depende_da_ordem_da_lista_de_tabelas(self):
        self.assertEqual(
            receita_base.hash_conteudo(self.con, ["PAI", "FILHO"]),
            receita_base.hash_conteudo(self.con, ["FILHO", "PAI"]))

    def test_hash_muda_quando_uma_celula_muda(self):
        antes = receita_base.hash_conteudo(self.con, ["PAI", "FILHO"])
        self.con.execute("UPDATE FILHO SET FILH_VALOR = 1.6 WHERE FILH_ID = 1")
        self.assertNotEqual(antes, receita_base.hash_conteudo(self.con, ["PAI", "FILHO"]))

    def test_hash_distingue_nulo_de_texto_vazio_e_tipo(self):
        a = sqlite3.connect(":memory:")
        b = sqlite3.connect(":memory:")
        for con in (a, b):
            con.execute("CREATE TABLE T (X)")
        a.execute("INSERT INTO T VALUES (NULL)")
        b.execute("INSERT INTO T VALUES ('')")
        self.assertNotEqual(receita_base.hash_conteudo(a, ["T"]), receita_base.hash_conteudo(b, ["T"]))
        c = sqlite3.connect(":memory:")
        c.execute("CREATE TABLE T (X)")
        c.execute("INSERT INTO T VALUES ('1')")
        d = sqlite3.connect(":memory:")
        d.execute("CREATE TABLE T (X)")
        d.execute("INSERT INTO T VALUES (1)")
        self.assertNotEqual(receita_base.hash_conteudo(c, ["T"]), receita_base.hash_conteudo(d, ["T"]))


class TestHashIgnorandoColunas(unittest.TestCase):
    def tabela(self, linhas):
        con = sqlite3.connect(":memory:")
        con.execute("CREATE TABLE T (ID INTEGER, DATA TEXT, NOME TEXT)")
        con.executemany("INSERT INTO T VALUES (?, ?, ?)", linhas)
        return con

    def test_igual_quando_so_a_coluna_ignorada_muda(self):
        a = self.tabela([(1, "2026-01-01", "x"), (2, "2026-01-02", "y")])
        b = self.tabela([(1, "2030-05-05 10:00:00", "x"), (2, None, "y")])
        ig = {"T": ["DATA"]}
        self.assertEqual(receita_base.hash_conteudo(a, ["T"], ig), receita_base.hash_conteudo(b, ["T"], ig))
        self.assertNotEqual(receita_base.hash_conteudo(a, ["T"]), receita_base.hash_conteudo(b, ["T"]))

    def test_muda_quando_celula_nao_ignorada_muda(self):
        a = self.tabela([(1, "2026-01-01", "x")])
        b = self.tabela([(1, "2026-01-01", "z")])
        ig = {"T": ["DATA"]}
        self.assertNotEqual(receita_base.hash_conteudo(a, ["T"], ig), receita_base.hash_conteudo(b, ["T"], ig))

    def test_coluna_ignorada_inexistente_falha(self):
        a = self.tabela([(1, "d", "x")])
        with self.assertRaisesRegex(RuntimeError, "NAO_EXISTE"):
            receita_base.hash_conteudo(a, ["T"], {"T": ["NAO_EXISTE"]})

    def test_montagens_diferentes_so_na_data_dao_o_mesmo_hash(self):
        con = receita_base.montar(FIXTURE)
        outra = receita_base.montar(FIXTURE)
        ig = {"PAI": ["PAI_NOME"]}
        outra.execute("UPDATE PAI SET PAI_NOME = 'outro'")
        self.assertEqual(
            receita_base.hash_conteudo(con, ["PAI"], ig), receita_base.hash_conteudo(outra, ["PAI"], ig))


class TestMeta(CopiaDaFixture):
    def test_meta_vai_e_volta(self):
        destino = self.tmp / "m.db"
        receita_base.reduzir(receita_base.montar(self.sql), ["PAI"], destino)
        meta = {"skill": "x", "base": {"versao": "V1", "hash": "sha256:00"}, "acentuação": "ção"}
        receita_base.gravar_meta(destino, meta)
        self.assertEqual(receita_base.ler_meta(destino), meta)
        novo = {"skill": "y"}
        receita_base.gravar_meta(destino, novo)
        self.assertEqual(receita_base.ler_meta(destino), novo)

    def test_ler_meta_de_base_sem_meta_falha(self):
        destino = self.tmp / "sem.db"
        sqlite3.connect(destino).close()
        with self.assertRaises(RuntimeError):
            receita_base.ler_meta(destino)


class TestBaseDaSkill(unittest.TestCase):
    def test_base_da_skill_bate_com_o_meta(self):
        meta = json.loads((ROOT / "conferencia/cbs-ibs-simples/meta.json").read_text(encoding="utf-8"))
        base = ROOT / "core/cd2-super/skills/cbs-ibs-simples/references/base-receita.db"
        self.assertEqual(receita_base.ler_meta(base), meta)
        con = sqlite3.connect(f"file:{base}?mode=ro", uri=True)
        self.addCleanup(con.close)
        self.assertEqual(receita_base.hash_conteudo(con, CONFIG["tabelas"], CONFIG["hash_ignorar_colunas"]), meta["base"]["hash"])
        self.assertEqual(meta["base"]["versao"], "V0073")

    def test_base_tem_so_as_tabelas_do_config(self):
        base = ROOT / "core/cd2-super/skills/cbs-ibs-simples/references/base-receita.db"
        con = sqlite3.connect(f"file:{base}?mode=ro", uri=True)
        self.addCleanup(con.close)
        nomes = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        self.assertEqual(nomes, set(CONFIG["tabelas"]) | {"CD2_META"})

    def test_base_tem_menos_de_3_mb(self):
        base = ROOT / "core/cd2-super/skills/cbs-ibs-simples/references/base-receita.db"
        self.assertLess(base.stat().st_size, 3 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
