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
        con = receita_base.montar(self.sql)
        ruim = self.tmp / "V0011__ruim.sql"
        ruim.write_text("INSERT INTO PAI VALUES (9, 'nove');\nINSERT INTO NAO_EXISTE VALUES (1);\n", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, r"V0011__ruim\.sql"):
            receita_base._executar(con, ruim, True)
        self.assertFalse(con.in_transaction)
        self.assertIsNone(con.execute("SELECT 1 FROM PAI WHERE PAI_ID = 9").fetchone())

    def test_migracao_com_begin_proprio_nao_ganha_outro(self):
        (self.sql / "manutencao" / "V0011__propria.sql").write_text(
            "BEGIN;\nINSERT INTO PAI VALUES (11, 'onze');\nCOMMIT;\n", encoding="utf-8")
        con = receita_base.montar(self.sql)
        self.assertEqual(con.execute("SELECT PAI_NOME FROM PAI WHERE PAI_ID = 11").fetchone()[0], "onze")

    def test_migracao_com_pragma_roda_fora_da_transacao(self):
        # PRAGMA foreign_keys não faz nada dentro de transação: o valor tem que mudar.
        (self.sql / "manutencao" / "V0011__pragma.sql").write_text(
            "PRAGMA foreign_keys = OFF;\nINSERT INTO ORDEM (ARQUIVO) VALUES ('V0011__pragma');\n", encoding="utf-8")
        con = receita_base.montar(self.sql)
        self.assertEqual(con.execute("PRAGMA foreign_keys").fetchone()[0], 0)

    def test_begin_de_trigger_e_nome_com_begin_nao_contam(self):
        trigger = ("CREATE TABLE T1 (BEGIN_DATE TEXT);\nCREATE TRIGGER TG AFTER INSERT ON T1\nBEGIN\n"
                   "  INSERT INTO ORDEM (ARQUIVO) VALUES ('x');\nEND;\n")
        self.assertFalse(receita_base._dona_da_transacao(trigger))
        self.assertFalse(receita_base._dona_da_transacao("BEGIN_DATE TEXT,\nBEGIN_X;\n"))
        self.assertFalse(receita_base._dona_da_transacao("-- BEGIN;\n/* PRAGMA foreign_keys = OFF; */\nSELECT 1;"))
        self.assertTrue(receita_base._dona_da_transacao("begin transaction;\nSELECT 1;\ncommit;"))

    def test_migracao_com_begin_de_trigger_e_falha_e_desfeita(self):
        (self.sql / "manutencao" / "V0011__trigger.sql").write_text(
            "CREATE TABLE T1 (BEGIN_DATE TEXT);\nCREATE TRIGGER TG AFTER INSERT ON T1\nBEGIN\n"
            "  INSERT INTO ORDEM (ARQUIVO) VALUES ('x');\nEND;\nINSERT INTO PAI VALUES (9, 'nove');\n"
            "INSERT INTO NAO_EXISTE VALUES (1);\n", encoding="utf-8")
        con = sqlite3.connect(":memory:", isolation_level=None)
        con.executescript("CREATE TABLE PAI (PAI_ID INTEGER, PAI_NOME TEXT);")
        with self.assertRaises(RuntimeError):
            receita_base._executar(con, self.sql / "manutencao" / "V0011__trigger.sql", True)
        self.assertIsNone(con.execute("SELECT 1 FROM PAI WHERE PAI_ID = 9").fetchone())
        self.assertFalse(con.in_transaction)

    def test_attach_e_negado_e_nao_cria_arquivo(self):
        alvo = self.tmp / "criado.db"
        (self.sql / "manutencao" / "V0011__attach.sql").write_text(
            f"ATTACH DATABASE '{alvo}' AS fora;\n", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, r"V0011__attach\.sql"):
            receita_base.montar(self.sql)
        self.assertFalse(alvo.exists())

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


class TestCommitDoClone(unittest.TestCase):
    def clone(self, head, refs=None):
        tmp = Path(tempfile.mkdtemp(prefix="receita_clone_"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / ".git").mkdir()
        (tmp / ".git" / "HEAD").write_text(head + "\n", encoding="utf-8")
        for nome, valor in (refs or {}).items():
            (tmp / ".git" / nome).parent.mkdir(parents=True, exist_ok=True)
            (tmp / ".git" / nome).write_text(valor + "\n", encoding="utf-8")
        return tmp

    def test_le_sha_direto_e_por_ref(self):
        sha = "ab" * 20
        self.assertEqual(receita_base._commit_do_clone(self.clone(sha)), sha)
        repo = self.clone("ref: refs/heads/main", {"refs/heads/main": sha})
        self.assertEqual(receita_base._commit_do_clone(repo), sha)

    def test_ref_fora_de_refs_ou_com_ponto_ponto_e_recusada(self):
        for ref in ("../../x", "refs/../../x", "/etc/passwd", "heads/main"):
            with self.assertRaisesRegex(RuntimeError, "Ref inválida"):
                receita_base._commit_do_clone(self.clone(f"ref: {ref}"))

    def test_valor_que_nao_e_sha_de_40_hex_e_recusado(self):
        for ruim in ("zz" * 20, "ab" * 19, "ab" * 21, "ABCDEF" + "0" * 34):
            with self.assertRaisesRegex(RuntimeError, "Commit inválido"):
                receita_base._commit_do_clone(self.clone(ruim))
        repo = self.clone("ref: refs/heads/main", {"refs/heads/main": "conteudo qualquer"})
        with self.assertRaisesRegex(RuntimeError, "Commit inválido"):
            receita_base._commit_do_clone(repo)


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
        self.assertRegex(meta["base"]["versao"], r"^V\d{4}$")

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
