"""Testes do robô de conferência (scripts/receita_robo.py)."""
import contextlib
import importlib.util
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
SKILL = "cbs-ibs-simples"
CONFIG = json.loads((ROOT / "conferencia" / SKILL / "config.json").read_text(encoding="utf-8"))
META = json.loads((ROOT / "conferencia" / SKILL / "meta.json").read_text(encoding="utf-8"))
EM, EN = chr(0x2014), chr(0x2013)


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


robo = load_module("receita_robo", ROOT / "scripts" / "receita_robo.py")
receita_base = load_module("receita_base", ROOT / "scripts" / "receita_base.py")
receita_paridade = load_module("receita_paridade", ROOT / "scripts" / "receita_paridade.py")

def rodar_cli(args):
    """Roda a linha de comando sem escrever no $GITHUB_OUTPUT de quem roda os testes."""
    ambiente = {k: v for k, v in os.environ.items() if k != "GITHUB_OUTPUT"}
    saida, erro = io.StringIO(), io.StringIO()
    with mock.patch.dict(os.environ, ambiente, clear=True), contextlib.redirect_stdout(saida), \
            contextlib.redirect_stderr(erro):
        codigo = robo.main(args)
    return codigo, saida.getvalue(), erro.getvalue()


CEM = {"casos": 12013, "iguais": 12013, "divergentes": []}
MENOS = {"casos": 12013, "iguais": 12000, "divergentes": ["caso-1"]}


def upstream(versao=None, commit_calc=None, commit_migr=None):
    return {
        "calculadora": {"versao": versao or META["calculadora"]["versao"],
                        "commit": commit_calc or META["calculadora"]["commit"]},
        "migracoes": {"commit": commit_migr or META["migracoes"]["commit"]},
    }


class ClassificarTest(unittest.TestCase):
    def test_nada_quando_nada_mudou(self):
        self.assertEqual(robo.classificar(META, upstream(), [], CONFIG), "nada")

    def test_tabelas_quando_so_as_migracoes_mudaram(self):
        u = upstream(commit_migr="a" * 40)
        self.assertEqual(robo.classificar(META, u, ["flyway/sql/manutencao/V0074__x.sql"], CONFIG), "tabelas")

    def test_commit_novo_das_migracoes_fora_de_flyway_sql_e_nada(self):
        u = upstream(commit_migr="a" * 40)
        self.assertEqual(robo.classificar(META, u, ["README.md", "src/main/java/X.java"], CONFIG), "nada")

    def test_release_sem_codigo(self):
        u = upstream(versao="1.0.3", commit_calc="b" * 40)
        caminhos = ["pom.xml", "README.md", "src/test/java/XTest.java", "flyway/sql/manutencao/V0074__x.sql"]
        self.assertEqual(robo.classificar(META, u, caminhos, CONFIG), "release-sem-codigo")

    def test_regra_quando_src_main_mudou(self):
        u = upstream(versao="1.0.3", commit_calc="b" * 40)
        self.assertEqual(robo.classificar(META, u, ["pom.xml", "src/main/java/br/X.java"], CONFIG), "regra")

    def test_prefixo_e_de_pasta_e_nao_de_texto(self):
        u = upstream(versao="1.0.3", commit_calc="b" * 40)
        self.assertEqual(robo.classificar(META, u, ["src/mainframe/X.java"], CONFIG), "release-sem-codigo")
        u = upstream(commit_migr="a" * 40)
        self.assertEqual(robo.classificar(META, u, ["flyway/sqlite.conf"], CONFIG), "nada")

    def test_commit_novo_da_calculadora_sem_versao_nova_e_nada(self):
        u = upstream(commit_calc="b" * 40)
        self.assertEqual(robo.classificar(META, u, ["src/main/java/X.java"], CONFIG), "nada")


class DecidirTest(unittest.TestCase):
    """Uma linha da tabela de decisão por teste (seção 6.4 do spec)."""

    def test_nada_sem_pr_termina(self):
        for paridade in (CEM, MENOS, None):
            self.assertEqual(robo.decidir("nada", paridade, False), "terminar")

    def test_tabelas_100_publica(self):
        self.assertEqual(robo.decidir("tabelas", CEM, False), "publicar")

    def test_tabelas_menos_de_100_abre_issue_de_divergencia(self):
        self.assertEqual(robo.decidir("tabelas", MENOS, False), "issue-divergencia")

    def test_release_sem_codigo_100_publica(self):
        self.assertEqual(robo.decidir("release-sem-codigo", CEM, False), "publicar")

    def test_release_sem_codigo_menos_de_100_abre_pr_de_regra(self):
        self.assertEqual(robo.decidir("release-sem-codigo", MENOS, False), "pr-regra")

    def test_regra_sem_pr_abre_pr(self):
        for paridade in (CEM, MENOS):
            self.assertEqual(robo.decidir("regra", paridade, False), "pr-regra")

    def test_pr_de_regra_aberto_congela_o_resto(self):
        for classe in ("nada", "tabelas", "release-sem-codigo"):
            for paridade in (CEM, MENOS):
                self.assertEqual(robo.decidir(classe, paridade, True), "congelado", classe)
        self.assertEqual(robo.decidir("nada", None, True), "congelado")

    def test_regra_com_pr_aberto_atualiza_o_pr(self):
        for paridade in (CEM, MENOS):
            self.assertEqual(robo.decidir("regra", paridade, True), "pr-regra")

    def test_sem_paridade_abre_issue_de_falha(self):
        for classe in ("tabelas", "release-sem-codigo", "regra"):
            for pr in (False, True):
                self.assertEqual(robo.decidir(classe, None, pr), "issue-falha", (classe, pr))

    def test_conferencia_vazia_nao_conta_como_100(self):
        self.assertEqual(robo.decidir("tabelas", {"casos": 0, "iguais": 0, "divergentes": []}, False),
                         "issue-falha")

    def test_classe_desconhecida_e_erro(self):
        with self.assertRaises(ValueError):
            robo.decidir("outra", CEM, False)


class LerParidadeTest(unittest.TestCase):
    def test_100_por_cento(self):
        texto = "12013 casos, 12013 iguais, 20 s\n"
        self.assertEqual(robo.ler_paridade(texto, 0), {"casos": 12013, "iguais": 12013, "divergentes": []})

    def test_divergentes(self):
        texto = "3 casos, 1 iguais, 1 s\nDIVERGE a\n  x: 1 != 2\nDIVERGE b\n  y: ausente no local\n"
        self.assertEqual(robo.ler_paridade(texto, 1),
                         {"casos": 3, "iguais": 1, "divergentes": ["a", "b"]})

    def test_sem_linha_ou_com_codigo_incoerente_e_none(self):
        self.assertIsNone(robo.ler_paridade("erro: A calculadora oficial não respondeu", 2))
        self.assertIsNone(robo.ler_paridade("", 0))
        self.assertIsNone(robo.ler_paridade("3 casos, 3 iguais, 1 s\n", 1))
        self.assertIsNone(robo.ler_paridade("3 casos, 2 iguais, 1 s\nDIVERGE a\n", 0))
        self.assertIsNone(robo.ler_paridade("3 casos, 3 iguais, 1 s\n", 2))


class ProximaVersaoTest(unittest.TestCase):
    def test_sobe_o_patch(self):
        self.assertEqual(robo.proxima_versao("0.6.0"), "0.6.1")
        self.assertEqual(robo.proxima_versao("0.6.9"), "0.6.10")
        self.assertEqual(robo.proxima_versao("1.12.99"), "1.12.100")

    def test_versao_fora_do_padrao_e_erro(self):
        for ruim in ("0.6", "v0.6.0", "0.6.0-rc1", ""):
            with self.assertRaises(ValueError):
                robo.proxima_versao(ruim)


ARQUIVOS_DA_COPIA = [
    "CHANGELOG.md",
    *robo.MANIFESTOS,
    f"conferencia/{SKILL}/config.json",
    f"conferencia/{SKILL}/meta.json",
    f"conferencia/{SKILL}/esperadas.json",
    CONFIG["base_destino"],
]


class AplicarPublicacaoTest(unittest.TestCase):
    def setUp(self):
        self.raiz = Path(tempfile.mkdtemp(prefix="receita_robo_"))
        self.addCleanup(shutil.rmtree, self.raiz, True)
        for rel in ARQUIVOS_DA_COPIA:
            destino = self.raiz / rel
            destino.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / rel, destino)
        self.versao = json.loads((ROOT / "core/cd2-super/plugin.core.json").read_text(encoding="utf-8"))["version"]

    def meta_nova(self, **trocas):
        meta = json.loads(json.dumps(META))
        meta["migracoes"]["commit"] = "c" * 40
        meta["base"] = {"versao": "V0074", "data": "2026-12-01", "hash": "sha256:" + "d" * 64}
        meta["conferencia"] = {"data": "2026-12-02", "casos": 12100, "iguais": 12100}
        for chave, valor in trocas.items():
            meta[chave] = valor
        return meta

    def test_tabelas(self):
        nova = robo.aplicar_publicacao(self.raiz, self.meta_nova(), "tabelas")
        self.assertEqual(nova, robo.proxima_versao(self.versao))
        for rel in robo.MANIFESTOS:
            dados = json.loads((self.raiz / rel).read_text(encoding="utf-8"))
            self.assertEqual(dados["version"], nova, rel)
        changelog = (self.raiz / "CHANGELOG.md").read_text(encoding="utf-8")
        topo = changelog.index("\n## ")
        self.assertTrue(changelog[topo:].startswith(f"\n## {nova}\n"))
        self.assertLess(changelog.index(f"## {nova}"), changelog.index(f"## {self.versao}"))
        secao = changelog.split(f"## {nova}", 1)[1].split(f"## {self.versao}", 1)[0]
        for agulha in ("cbs-ibs-simples", META["base"]["versao"], "V0074", "12.100", "02/12/2026", "`ccccccc`",
                       "A conta não mudou"):
            self.assertIn(agulha, secao, agulha)
        self.assertNotIn("código de cálculo", secao)
        self.assertNotRegex(secao, f"[{EM}{EN}]")

    def test_meta_vai_para_o_meta_json_a_base_e_as_esperadas(self):
        meta = self.meta_nova()
        robo.aplicar_publicacao(self.raiz, meta, "tabelas")
        pasta = self.raiz / "conferencia" / SKILL
        self.assertEqual(json.loads((pasta / "meta.json").read_text(encoding="utf-8")), meta)
        self.assertEqual(receita_base.ler_meta(self.raiz / CONFIG["base_destino"]), meta)
        esperadas = receita_paridade.ler_json((pasta / "esperadas.json").read_text(encoding="utf-8"))
        self.assertEqual(json.loads(json.dumps(esperadas["meta"])), meta)
        original = receita_paridade.ler_json((ROOT / "conferencia" / SKILL / "esperadas.json").read_text(encoding="utf-8"))
        self.assertEqual(esperadas["casos"], original["casos"])

    def test_release_sem_codigo_diz_que_o_codigo_de_calculo_nao_mudou(self):
        meta = self.meta_nova(calculadora={**META["calculadora"], "versao": "1.0.3", "commit": "e" * 40})
        nova = robo.aplicar_publicacao(self.raiz, meta, "release-sem-codigo")
        changelog = (self.raiz / "CHANGELOG.md").read_text(encoding="utf-8")
        secao = changelog.split(f"## {nova}", 1)[1].split(f"## {self.versao}", 1)[0]
        for agulha in ("1.0.3", META["calculadora"]["versao"], "código de cálculo", "`src/main`", "não mudou"):
            self.assertIn(agulha, secao, agulha)
        self.assertNotRegex(secao, f"[{EM}{EN}]")
        texto_tabelas = robo.texto_changelog(META, self.meta_nova(), "tabelas", nova)
        self.assertNotEqual(texto_tabelas, robo.texto_changelog(META, meta, "release-sem-codigo", nova))

    def test_cli_publicar_conta_a_mudanca_a_partir_da_meta_do_comeco(self):
        # No workflow o meta.json em disco já é o da base nova quando o publicar roda.
        antiga = json.loads(json.dumps(META))
        antiga["migracoes"]["commit"] = "1" * 40
        antiga["base"] = {"versao": "V0059", "data": "2026-09-30", "hash": "sha256:" + "0" * 64}
        estado = {"skill": SKILL, "classe": "tabelas", "meta": antiga,
                  "upstream": {"calculadora": {"versao": META["calculadora"]["versao"], "commit": META["calculadora"]["commit"]},
                               "migracoes": {"commit": META["migracoes"]["commit"]}}}
        (self.raiz / "estado.json").write_text(json.dumps(estado), encoding="utf-8")
        (self.raiz / "paridade.txt").write_text("12013 casos, 12013 iguais, 20 s\n", encoding="utf-8")
        args = ["--raiz", str(self.raiz), "publicar", "--estado", str(self.raiz / "estado.json"),
                "--paridade", str(self.raiz / "paridade.txt"), "--codigo", "0", "--data", "2026-12-02"]
        codigo, saida, _ = rodar_cli(args)
        self.assertEqual(codigo, 0)
        self.assertIn(f"versao={robo.proxima_versao(self.versao)}", saida)
        nova = robo.proxima_versao(self.versao)
        changelog = (self.raiz / "CHANGELOG.md").read_text(encoding="utf-8")
        secao = changelog.split(f"## {nova}", 1)[1].split(f"## {self.versao}", 1)[0]
        self.assertIn(f"da V0059 para a {META['base']['versao']}", secao)
        meta = json.loads((self.raiz / "conferencia" / SKILL / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["conferencia"], {"data": "2026-12-02", "casos": 12013, "iguais": 12013})

    def test_cli_publicar_recusa_conferencia_abaixo_de_100(self):
        estado = {"skill": SKILL, "classe": "tabelas", "meta": META,
                  "upstream": {"calculadora": META["calculadora"], "migracoes": META["migracoes"]}}
        (self.raiz / "estado.json").write_text(json.dumps(estado), encoding="utf-8")
        (self.raiz / "paridade.txt").write_text("3 casos, 2 iguais, 1 s\nDIVERGE a\n", encoding="utf-8")
        args = ["--raiz", str(self.raiz), "publicar", "--estado", str(self.raiz / "estado.json"),
                "--paridade", str(self.raiz / "paridade.txt"), "--codigo", "1"]
        codigo, _, erro = rodar_cli(args)
        self.assertEqual(codigo, 1)
        self.assertIn("100%", erro)
        self.assertEqual(json.loads((self.raiz / "core/cd2-super/plugin.core.json").read_text(encoding="utf-8"))["version"],
                         self.versao)

    def test_outra_classe_nao_publica(self):
        for classe in ("nada", "regra"):
            with self.assertRaises(ValueError):
                robo.aplicar_publicacao(self.raiz, self.meta_nova(), classe)
        self.assertEqual(json.loads((self.raiz / "core/cd2-super/plugin.core.json").read_text(encoding="utf-8"))["version"],
                         self.versao)

    def test_manifestos_com_versoes_diferentes_nao_publicam(self):
        rel = robo.MANIFESTOS[1]
        caminho = self.raiz / rel
        caminho.write_text(caminho.read_text(encoding="utf-8").replace(f'"{self.versao}"', '"9.9.9"', 1),
                           encoding="utf-8")
        with self.assertRaises(RuntimeError):
            robo.aplicar_publicacao(self.raiz, self.meta_nova(), "tabelas")

    def test_publicar_duas_vezes_sobe_duas_vezes_mas_o_robo_nao_chega_la(self):
        # A repetição é barrada antes: com a meta publicada, a Receita igual vira "nada".
        meta = self.meta_nova()
        robo.aplicar_publicacao(self.raiz, meta, "tabelas")
        u = {"calculadora": {"versao": meta["calculadora"]["versao"], "commit": meta["calculadora"]["commit"]},
             "migracoes": {"commit": meta["migracoes"]["commit"]}}
        self.assertEqual(robo.classificar(meta, u, ["flyway/sql/manutencao/V0074__x.sql"], CONFIG), "nada")
        self.assertEqual(robo.decidir("nada", None, False), "terminar")


class CliDetectarDecidirTest(unittest.TestCase):
    def setUp(self):
        self.raiz = Path(tempfile.mkdtemp(prefix="receita_robo_cli_"))
        self.addCleanup(shutil.rmtree, self.raiz, True)
        pasta = self.raiz / "conferencia" / SKILL
        pasta.mkdir(parents=True)
        for nome in ("config.json", "meta.json"):
            shutil.copy2(ROOT / "conferencia" / SKILL / nome, pasta / nome)

    def arquivo(self, nome, conteudo):
        caminho = self.raiz / nome
        caminho.write_text(conteudo if isinstance(conteudo, str) else json.dumps(conteudo), encoding="utf-8")
        return str(caminho)

    def detectar(self, up, caminhos):
        codigo, saida, _ = rodar_cli(["--raiz", str(self.raiz), "detectar", "--skill", SKILL,
                                      "--upstream", self.arquivo("upstream.json", up),
                                      "--caminhos", self.arquivo("caminhos.txt", "\n".join(caminhos)),
                                      "--saida", str(self.raiz / "estado.json")])
        self.assertEqual(codigo, 0)
        return saida

    def decidir(self, *extra):
        codigo, saida, _ = rodar_cli(["--raiz", str(self.raiz), "decidir", "--estado", str(self.raiz / "estado.json"),
                                      "--paridade", self.arquivo("paridade.txt", "12013 casos, 12013 iguais, 20 s\n"),
                                      "--codigo", "0", "--resultado-conferencia", "success", *extra])
        self.assertEqual(codigo, 0)
        return saida

    def test_tabelas_na_main_publica(self):
        self.assertIn("classe=tabelas", self.detectar(upstream(commit_migr="a" * 40), ["flyway/sql/manutencao/V0074__x.sql"]))
        self.assertIn("acao=publicar", self.decidir("--ramo", "refs/heads/main"))

    def test_fora_da_main_so_confere(self):
        self.detectar(upstream(commit_migr="a" * 40), ["flyway/sql/manutencao/V0074__x.sql"])
        saida = self.decidir("--ramo", "refs/heads/outro")
        self.assertIn("acao=terminar", saida)
        self.assertIn("na main seria publicar", saida)

    def test_meta_mudou_no_meio_nao_age(self):
        self.detectar(upstream(commit_migr="a" * 40), ["flyway/sql/manutencao/V0074__x.sql"])
        caminho = self.raiz / "conferencia" / SKILL / "meta.json"
        meta = json.loads(caminho.read_text(encoding="utf-8"))
        meta["calculadora"]["versao"] = "9.9.9"
        caminho.write_text(json.dumps(meta), encoding="utf-8")
        self.assertIn("acao=terminar", self.decidir("--ramo", "refs/heads/main"))

    def test_conferencia_que_nao_terminou_vira_falha(self):
        codigo, saida, _ = rodar_cli(["--raiz", str(self.raiz), "decidir", "--estado", str(self.raiz / "nao-existe.json"),
                                      "--resultado-conferencia", "sem artefato"])
        self.assertEqual(codigo, 0)
        self.assertIn("acao=issue-falha", saida)
        self.detectar(upstream(commit_migr="a" * 40), ["flyway/sql/manutencao/V0074__x.sql"])
        codigo, saida, _ = rodar_cli(["--raiz", str(self.raiz), "decidir", "--estado", str(self.raiz / "estado.json"),
                                      "--resultado-conferencia", "failure",
                                      "--etapa", self.arquivo("etapa.txt", "Comparar as duas bases")])
        self.assertIn("acao=issue-falha", saida)
        self.assertIn("Comparar as duas bases", saida)


class TextosTest(unittest.TestCase):
    UP = {"calculadora": {"versao": "1.0.3", "commit": "b" * 40},
          "migracoes": {"commit": "a" * 40},
          "base": {"versao": "V0074", "data": "2026-12-01"}}

    def test_pr_regra_comeca_pelo_aviso_de_tabelas_paradas(self):
        texto = robo.texto_pr_regra(META, self.UP, MENOS, "diff --git a/src/main/X.java b/src/main/X.java\n+linha\n")
        primeira = texto.lstrip().splitlines()[0]
        self.assertIn("tabelas", primeira.lower())
        self.assertIn("paradas", primeira.lower())
        for agulha in ("1.0.2", "1.0.3", "12.013", "12.000", "caso-1", "+linha", "- [ ]",
                       f"compare/{META['calculadora']['commit']}...{'b' * 40}"):
            self.assertIn(agulha, texto, agulha)
        self.assertNotRegex(texto, f"[{EM}{EN}]")
        self.assertNotRegex(texto, "[\U0001F300-\U0001FAFF☀-➿]")

    def test_pr_regra_corta_diff_grande_e_cabe_no_limite_do_github(self):
        diff = "".join(f"+linha {i} do java que mudou\n" for i in range(20000))
        texto = robo.texto_pr_regra(META, self.UP, CEM, diff)
        self.assertLessEqual(len(texto), robo.LIMITE_CORPO)
        self.assertIn("cortado", texto)
        self.assertIn("https://github.com/ReceitaFederal/calculadora-rtc-simples-nacional/compare/", texto)

    def test_pr_regra_com_cerca_dentro_do_diff(self):
        texto = robo.texto_pr_regra(META, self.UP, CEM, "+ /** ```java */\n")
        self.assertIn("````", texto)

    def test_issues_sem_travessao(self):
        textos = [
            robo.texto_issue("issue-divergencia", skill=SKILL, meta=META, upstream=self.UP, paridade=MENOS,
                             execucao="https://github.com/cd2tec/super/actions/runs/1"),
            robo.texto_issue("issue-falha", skill=SKILL, meta=META, upstream=self.UP, motivo="Compilar a calculadora oficial",
                             execucao="https://github.com/cd2tec/super/actions/runs/1"),
            robo.texto_issue("issue-gpt", skill=SKILL, meta=META, versao_no_ar="0.6.0", versao_nova="0.6.1"),
            robo.texto_issue("congelado", skill=SKILL, meta=META, upstream=self.UP, paridade=CEM, hoje="2026-12-02"),
            robo.texto_issue("regra-atualizada", skill=SKILL, meta=META, upstream=self.UP, paridade=CEM, hoje="2026-12-02"),
        ]
        for texto in textos:
            self.assertNotRegex(texto, f"[{EM}{EN}]")
            self.assertNotRegex(texto, "[\U0001F300-\U0001FAFF☀-➿]")
        self.assertIn("caso-1", textos[0])
        self.assertIn("Compilar a calculadora oficial", textos[1])
        self.assertIn("0.6.0", textos[2])
        self.assertIn("0.6.1", textos[2])
        self.assertIn("paradas", textos[3])

    def test_issue_gpt_guarda_a_versao_no_ar(self):
        primeiro = robo.texto_issue("issue-gpt", skill=SKILL, meta=META, versao_no_ar="0.6.0", versao_nova="0.6.1")
        self.assertEqual(robo.versao_no_ar(primeiro), "0.6.0")
        self.assertIsNone(robo.versao_no_ar("corpo escrito por gente"))

    def test_titulos_sao_estaveis(self):
        self.assertEqual(robo.titulo("issue-gpt", SKILL), "Republicar o GPT cbs-ibs-simples")
        titulos = {robo.titulo(t, SKILL) for t in ("pr-regra", "issue-divergencia", "issue-falha", "issue-gpt")}
        self.assertEqual(len(titulos), 4)
        for t in titulos:
            self.assertIn(SKILL, t)

    def test_um_comentario_por_estado_e_por_dia(self):
        marca = robo.marca("congelado", self.UP, "2026-12-02")
        self.assertTrue(robo.deve_comentar([], "congelado", self.UP, "2026-12-02"))
        self.assertFalse(robo.deve_comentar([f"texto\n{marca}"], "congelado", self.UP, "2026-12-02"))
        self.assertFalse(robo.deve_comentar([f"texto\n{marca}"], "congelado", self.UP, "2026-12-03"))
        outro = {**self.UP, "migracoes": {"commit": "f" * 40}}
        self.assertFalse(robo.deve_comentar([f"texto\n{marca}"], "congelado", outro, "2026-12-02"))
        self.assertTrue(robo.deve_comentar([f"texto\n{marca}"], "congelado", outro, "2026-12-03"))
        self.assertTrue(robo.deve_comentar([f"texto\n{marca}"], "issue-falha", outro, "2026-12-03"))

    def test_falha_comenta_todo_dia_mesmo_com_a_receita_parada(self):
        marca = robo.marca("issue-falha", self.UP, "2026-12-02")
        self.assertFalse(robo.deve_comentar([marca], "issue-falha", self.UP, "2026-12-02"))
        self.assertTrue(robo.deve_comentar([marca], "issue-falha", self.UP, "2026-12-03"))


class CompararBasesTest(unittest.TestCase):
    def base(self, linhas, extra=None):
        con = sqlite3.connect(":memory:")
        con.execute("CREATE TABLE VERSAO_BASE_DADO (VRBD_ID INTEGER, VRBD_DATA TEXT, VRBD_VERSAO_BASE_DADO TEXT)")
        con.execute("CREATE TABLE T (A INTEGER)")
        con.executemany("INSERT INTO T VALUES (?)", [(x,) for x in linhas])
        con.execute("INSERT INTO VERSAO_BASE_DADO VALUES (1, datetime('now'), 'V1')")
        if extra:
            con.execute(f"CREATE TABLE {extra} (X)")
        return con

    def test_iguais_ignorando_o_historico_do_flyway_e_a_data(self):
        flyway = self.base([1, 2], extra="flyway_schema_history")
        flyway.execute("UPDATE VERSAO_BASE_DADO SET VRBD_DATA = '2001-01-01'")
        self.assertEqual(robo.comparar_bases(flyway, self.base([2, 1]), CONFIG["hash_ignorar_colunas"]), [])

    def test_diferentes(self):
        self.assertTrue(robo.comparar_bases(self.base([1, 2]), self.base([1, 3]), CONFIG["hash_ignorar_colunas"]))
        self.assertTrue(robo.comparar_bases(self.base([1], extra="NOVA"), self.base([1]), CONFIG["hash_ignorar_colunas"]))


if __name__ == "__main__":
    unittest.main()
