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

    def test_mesma_versao_com_outro_commit_nunca_publica(self):
        # tag movida: não é nada e não é tabela, é falha
        u = upstream(commit_calc="b" * 40)
        with self.assertRaises(robo.MudancaInvalida):
            robo.classificar(META, u, ["src/main/java/X.java"], CONFIG)

    def test_mesma_versao_mesmo_commit_e_nada(self):
        self.assertEqual(robo.classificar(META, upstream(versao="v" + META["calculadora"]["versao"]), ["pom.xml"], CONFIG),
                         "nada")

    def test_versao_para_tras_nunca_publica(self):
        # releases/latest segue a marca de "latest" e a data, não a ordem das versões
        u = upstream(versao="1.0.1", commit_calc="b" * 40)
        with self.assertRaises(robo.MudancaInvalida) as erro:
            robo.classificar(META, u, ["pom.xml"], CONFIG)
        self.assertIn("anterior", str(erro.exception))
        with self.assertRaises(robo.MudancaInvalida):
            robo.classificar(META, upstream(versao="1.0.2-sem-numero!", commit_calc="b" * 40), [], CONFIG)
        with self.assertRaises(robo.MudancaInvalida):
            robo.classificar(META, upstream(versao="x", commit_calc="b" * 40), [], CONFIG)

    def test_versao_maior_compara_por_numero(self):
        u = upstream(versao="1.0.10", commit_calc="b" * 40)
        self.assertEqual(robo.classificar({**META, "calculadora": {**META["calculadora"], "versao": "1.0.9"}},
                                          u, ["pom.xml"], CONFIG), "release-sem-codigo")

    def test_partes_da_versao_como_no_calc(self):
        self.assertEqual(robo.partes_da_versao("v1.0.2"), (1, 0, 2))
        self.assertEqual(robo.partes_da_versao("1.0.0"), (1,))
        self.assertEqual(robo.partes_da_versao("1.0.3-rc1"), (1, 0, 3))
        self.assertIsNone(robo.partes_da_versao("abc"))
        self.assertLess(robo.partes_da_versao("1.0.9"), robo.partes_da_versao("1.0.10"))


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
                       "O código de cálculo não mudou", "o resultado pode mudar"):
            self.assertIn(agulha, secao, agulha)
        # a base mudou: a conta pode dar outro resultado, então o texto não diz que ela não mudou
        self.assertNotIn("A conta não mudou", secao)
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

    def test_release_sem_codigo_com_a_mesma_base_diz_que_a_conta_nao_mudou(self):
        meta = json.loads(json.dumps(META))
        meta["calculadora"] = {**META["calculadora"], "versao": "1.0.3", "commit": "e" * 40}
        meta["conferencia"] = {"data": "2026-12-02", "casos": 12013, "iguais": 12013}
        texto = robo.texto_changelog(META, meta, "release-sem-codigo", "0.6.1")
        self.assertIn("A conta não mudou.", texto)
        self.assertIn(f"segue na {META['base']['versao']}", texto)
        mudou = json.loads(json.dumps(meta))
        mudou["base"]["hash"] = "sha256:" + "9" * 64
        texto = robo.texto_changelog(META, mudou, "release-sem-codigo", "0.6.1")
        self.assertNotIn("A conta não mudou", texto)
        self.assertIn("com conteúdo novo", texto)

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
        self.assertIn("na main a ação seria publicar", saida)

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


def rodar_cli_com_saida(args):
    """Roda a linha de comando com um $GITHUB_OUTPUT de verdade e devolve (código, stdout, linhas da saída)."""
    with tempfile.TemporaryDirectory() as pasta:
        arquivo = Path(pasta) / "github_output"
        arquivo.write_text("", encoding="utf-8")
        ambiente = {**{k: v for k, v in os.environ.items() if k != "GITHUB_OUTPUT"}, "GITHUB_OUTPUT": str(arquivo)}
        saida = io.StringIO()
        with mock.patch.dict(os.environ, ambiente, clear=True), contextlib.redirect_stdout(saida), \
                contextlib.redirect_stderr(io.StringIO()):
            codigo = robo.main(args)
        return codigo, saida.getvalue(), arquivo.read_text(encoding="utf-8").splitlines()


class SaidaTest(unittest.TestCase):
    def test_recusa_quebra_de_linha_nul_e_chave_desconhecida(self):
        for valor in ("terminar\npr=$(id)", "terminar\r", "terminar\0"):
            with self.assertRaises(ValueError):
                robo._saida(acao=valor)
        with self.assertRaises(ValueError):
            robo._saida(motivo="qualquer texto")

    def test_cada_saida_tem_padrao(self):
        for chave, ruim in (("acao", "publicar-tudo"), ("pr", "$(id)"), ("pr", "12a"), ("versao", "1.0.2;id"),
                            ("base", "V73"), ("classe", "outra"), ("disparar", "sim")):
            with self.assertRaises(ValueError, msg=(chave, ruim)):
                robo._saida(**{chave: ruim})

    def test_carga_do_revisor_na_etapa_nao_vira_saida(self):
        # etapa.txt vem do artefato do job que roda o código da Receita
        with tempfile.TemporaryDirectory() as pasta:
            pasta = Path(pasta)
            etapa = pasta / "etapa.txt"
            etapa.write_text("Compilar\npr=$(id)\nacao=congelado", encoding="utf-8")
            motivo = pasta / "motivo.txt"
            codigo, _, linhas = rodar_cli_com_saida(
                ["decidir", "--estado", str(pasta / "nao-existe.json"), "--resultado-conferencia", "failure",
                 "--etapa", str(etapa), "--ramo", "refs/heads/main", "--motivo-saida", str(motivo)])
            self.assertEqual(codigo, 0)
            self.assertEqual(linhas, ["acao=issue-falha", "pr="])
            self.assertIn("pr=$(id)", motivo.read_text(encoding="utf-8"))
            # e o texto da issue trata o motivo como dado
            texto = robo.texto_issue("issue-falha", skill=SKILL, meta=META, motivo=motivo.read_text(encoding="utf-8"))
            linha = next(l for l in texto.splitlines() if l.startswith("Motivo:"))
            self.assertNotIn("\n", linha)
            self.assertIn("\\(id\\)", linha)

    def test_pr_com_lixo_vira_vazio(self):
        with tempfile.TemporaryDirectory() as pasta:
            codigo, _, linhas = rodar_cli_com_saida(
                ["decidir", "--estado", str(Path(pasta) / "x.json"), "--pr", "1\npr=2", "--resultado-conferencia", "x"])
            self.assertEqual(linhas, ["acao=issue-falha", "pr="])

    def test_texto_seguro_desarma_marcas_e_markdown(self):
        seguro = robo.texto_seguro("a <!-- cd2-robo tipo=congelado estado=x dia=2026-01-01 --> [link](http://x) `c`\nb")
        self.assertNotIn("<!--", seguro)
        self.assertNotIn("\n", seguro)
        self.assertNotIn("](", seguro)
        self.assertEqual(robo._MARCA.findall(seguro), [])


def _artefato(destino: Path, classe="tabelas"):
    """Um artefato válido montado com os arquivos de hoje do repositório (sem mudança na Receita)."""
    destino.mkdir(parents=True, exist_ok=True)
    estado = {"skill": SKILL, "classe": classe, "meta": META,
              "upstream": {"calculadora": {"versao": META["calculadora"]["versao"], "commit": META["calculadora"]["commit"]},
                           "migracoes": {"commit": META["migracoes"]["commit"]}}}
    (destino / "estado.json").write_text(json.dumps(estado), encoding="utf-8")
    shutil.copy2(ROOT / "conferencia" / SKILL / "meta.json", destino / "meta.json")
    shutil.copy2(ROOT / CONFIG["base_destino"], destino / "base-receita.db")
    shutil.copy2(ROOT / CONFIG["base_destino"], destino / "base-completa.db")
    shutil.copy2(ROOT / "conferencia" / SKILL / "esperadas.json", destino / "esperadas.json")
    (destino / "paridade.txt").write_text("12013 casos, 12013 iguais, 20 s\n", encoding="utf-8")
    (destino / "paridade-codigo.txt").write_text("0\n", encoding="utf-8")
    return estado


class ArtefatoTest(unittest.TestCase):
    def setUp(self):
        self.pasta = Path(tempfile.mkdtemp(prefix="receita_robo_art_"))
        self.addCleanup(shutil.rmtree, self.pasta, True)
        self.estado = _artefato(self.pasta)

    def problemas(self):
        return robo.validar_artefato(ROOT, SKILL, self.pasta)[1]

    def test_artefato_bom_passa(self):
        self.assertEqual(self.problemas(), [])

    def test_base_com_linha_mudada_nao_tem_o_hash_do_meta(self):
        con = sqlite3.connect(self.pasta / "base-receita.db")
        con.execute("UPDATE PERCENTUAL_REDUCAO SET PERE_VALOR = PERE_VALOR + 1 WHERE rowid = 1")
        con.commit()
        con.close()
        self.assertTrue(any("hash" in p for p in self.problemas()))

    def test_meta_diferente_da_cd2_meta(self):
        meta = json.loads(json.dumps(META))
        meta["base"]["data"] = "2026-01-01"
        (self.pasta / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        self.assertTrue(any("CD2_META" in p for p in self.problemas()))

    def test_valores_fora_do_padrao(self):
        for troca in (lambda e: e["upstream"]["calculadora"].update(commit="$(id)"),
                      lambda e: e["upstream"]["calculadora"].update(versao="1.0.2\nacao=publicar"),
                      lambda e: e.update(classe="publicar"),
                      lambda e: e.update(skill="outra")):
            estado = json.loads(json.dumps(self.estado))
            troca(estado)
            (self.pasta / "estado.json").write_text(json.dumps(estado), encoding="utf-8")
            self.assertTrue(self.problemas(), estado)

    def test_meta_do_artefato_nao_pode_mexer_na_calculadora_nem_na_conferencia(self):
        meta = json.loads(json.dumps(META))
        meta["conferencia"]["iguais"] = 1
        (self.pasta / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        self.assertTrue(any("conferencia" in p for p in self.problemas()))

    def test_arquivo_grande_demais_ou_faltando(self):
        (self.pasta / "paridade-codigo.txt").write_text("0" * 50, encoding="utf-8")
        self.assertTrue(any("paridade-codigo.txt" in p for p in self.problemas()))
        (self.pasta / "esperadas.json").unlink()
        self.assertTrue(any("falta esperadas.json" in p for p in self.problemas()))

    def test_esperadas_com_id_estranho(self):
        esperadas = json.loads((self.pasta / "esperadas.json").read_text(encoding="utf-8"))
        esperadas["casos"][0]["id"] = "<!-- cd2-robo -->"
        (self.pasta / "esperadas.json").write_text(json.dumps(esperadas), encoding="utf-8")
        self.assertTrue(any("esperadas" in p for p in self.problemas()))


class ReconferirTest(unittest.TestCase):
    def estado(self, classe="tabelas", migr="a" * 40, versao=None, calc=None):
        return {"skill": SKILL, "classe": classe, "meta": META,
                "upstream": {"calculadora": {"versao": versao or META["calculadora"]["versao"],
                                             "commit": calc or META["calculadora"]["commit"]},
                             "migracoes": {"commit": migr}}}

    def test_igual_ao_artefato_passa(self):
        estado = self.estado()
        compare = {"files": [{"filename": "flyway/sql/manutencao/V0074__x.sql"}, {"filename": "README.md"}]}
        self.assertEqual(robo.reconferir(META, CONFIG, estado, estado["upstream"], None, compare), [])

    def test_estado_da_receita_diferente_do_artefato(self):
        estado = self.estado()
        api = json.loads(json.dumps(estado["upstream"]))
        api["migracoes"]["commit"] = "b" * 40
        self.assertTrue(robo.reconferir(META, CONFIG, estado, api, None, {"files": []}))

    def test_classe_diferente_da_do_artefato(self):
        # o artefato diz "tabelas", mas a comparação não mexe em flyway/sql
        estado = self.estado()
        problemas = robo.reconferir(META, CONFIG, estado, estado["upstream"], None, {"files": [{"filename": "README.md"}]})
        self.assertTrue(any("classe" in p for p in problemas))

    def test_artefato_que_esconde_src_main(self):
        estado = self.estado(classe="release-sem-codigo", migr=META["migracoes"]["commit"], versao="1.0.3", calc="c" * 40)
        compare = {"files": [{"filename": "pom.xml"}, {"filename": "src/main/java/X.java"}]}
        self.assertTrue(robo.reconferir(META, CONFIG, estado, estado["upstream"], compare, None))

    def test_comparacao_cortada_so_confirma_regra(self):
        estado = self.estado(classe="release-sem-codigo", migr=META["migracoes"]["commit"], versao="1.0.3", calc="c" * 40)
        compare = {"files": [{"filename": f"docs/{i}.md"} for i in range(300)]}
        problemas = robo.reconferir(META, CONFIG, estado, estado["upstream"], compare, None)
        self.assertTrue(any("300" in p for p in problemas))

    def test_versao_para_tras_e_falha(self):
        estado = self.estado(classe="release-sem-codigo", migr=META["migracoes"]["commit"], versao="1.0.1", calc="c" * 40)
        problemas = robo.reconferir(META, CONFIG, estado, estado["upstream"], {"files": []}, None)
        self.assertTrue(any("anterior" in p for p in problemas))

    def test_cli_reconferir_com_artefato_e_api(self):
        with tempfile.TemporaryDirectory() as pasta:
            pasta = Path(pasta)
            _artefato(pasta / "art", classe="nada")
            motivo = pasta / "motivo.txt"
            codigo, _, _ = rodar_cli_com_saida(
                ["reconferir", "--skill", SKILL, "--artefato", str(pasta / "art"), "--tag", META["calculadora"]["versao"],
                 "--commit-calc", META["calculadora"]["commit"], "--commit-migr", META["migracoes"]["commit"],
                 "--motivo-saida", str(motivo)])
            self.assertEqual(codigo, 0)
            codigo, _, linhas = rodar_cli_com_saida(
                ["reconferir", "--skill", SKILL, "--artefato", str(pasta / "art"), "--tag", META["calculadora"]["versao"],
                 "--commit-calc", "d" * 40, "--commit-migr", META["migracoes"]["commit"], "--motivo-saida", str(motivo)])
            self.assertEqual((codigo, linhas), (1, []))
            self.assertIn("API do GitHub", motivo.read_text(encoding="utf-8"))


REPO = "cd2tec/super"
ROBO = {"login": "app/github-actions", "is_bot": True}
GENTE = {"login": "alguem", "is_bot": False}
TITULO_PR = robo.titulo("pr-regra", SKILL)


def pr(numero, **trocas):
    base = {"number": numero, "title": TITULO_PR, "headRefName": f"receita/{SKILL}-1.0.3", "isCrossRepository": False,
            "headRepository": {"name": "super"}, "headRepositoryOwner": {"login": "cd2tec"}, "author": ROBO}
    base.update(trocas)
    return base


class QuemERoboTest(unittest.TestCase):
    def test_pr_de_fora_nao_congela_nem_recebe_comentario(self):
        prs = [pr(1, isCrossRepository=True, headRepositoryOwner={"login": "estranho"}, author=GENTE),
               pr(2, author=GENTE),
               pr(3, headRefName="main"),
               pr(4, headRepository={"name": "outro"}),
               pr(5, title=TITULO_PR + " "),
               pr(6)]
        self.assertEqual(robo.pr_do_robo(prs, TITULO_PR, REPO), 6)
        self.assertIsNone(robo.pr_do_robo(prs[:5], TITULO_PR, REPO))
        self.assertIsNone(robo.pr_do_robo("lixo", TITULO_PR, REPO))

    def test_issue_so_do_robo(self):
        titulo_issue = robo.titulo("issue-gpt", SKILL)
        issues = [{"number": 7, "title": titulo_issue, "author": GENTE},
                  {"number": 8, "title": titulo_issue, "author": {"login": "github-actions", "is_bot": True}}]
        self.assertEqual(robo.issue_do_robo(issues, titulo_issue), 8)
        self.assertIsNone(robo.issue_do_robo(issues[:1], titulo_issue))

    def test_marcas_de_fora_nao_contam(self):
        # comentários como a API REST devolve (gh api .../issues/<n>/comments --paginate --slurp: páginas)
        up = TextosTest.UP
        marca = robo.marca("congelado", up, "2026-12-02")
        paginas = [[{"user": {"login": "alguem", "type": "User"}, "body": marca},
                    {"user": {"login": "github-actions[bot]", "type": "User"}, "body": marca},
                    {"user": {"login": "outro-bot[bot]", "type": "Bot"}, "body": marca}]]
        corpos = robo.corpos_do_robo(paginas)
        self.assertEqual(corpos, [])
        self.assertTrue(robo.deve_comentar(corpos, "congelado", up, "2026-12-02"))
        paginas.append([{"user": {"login": "github-actions[bot]", "type": "Bot"}, "body": marca}])
        self.assertEqual(robo.corpos_do_robo(paginas), [marca])
        self.assertFalse(robo.deve_comentar(robo.corpos_do_robo(paginas), "congelado", up, "2026-12-02"))

    def test_comentario_no_formato_do_gh_json_nao_e_confiavel(self):
        # o gh --json comments dá só {"author": {"login": "github-actions"}}, sem is_bot: nunca vale
        marca = robo.marca("congelado", TextosTest.UP, "2026-12-02")
        for formato in ({"comments": [{"author": {"login": "github-actions"}, "body": marca}]},
                        [{"author": {"login": "github-actions"}, "body": marca}],
                        [{"author": {"login": "github-actions", "is_bot": True}, "body": marca}]):
            self.assertEqual(robo.corpos_do_robo(formato), [], formato)

    def test_corpo_do_robo_conta_como_ja_comentado(self):
        # a issue (ou o PR) que o robô abriu já traz a marca no corpo: o dia seguinte igual não comenta
        with tempfile.TemporaryDirectory() as pasta:
            pasta = Path(pasta)
            _artefato(pasta / "art")
            estado = json.loads((pasta / "art" / "estado.json").read_text(encoding="utf-8"))
            corpo = robo.texto_issue("issue-divergencia", skill=SKILL, meta=META, upstream=estado["upstream"],
                                     paridade=MENOS, hoje="2026-12-02")
            (pasta / "corpo.json").write_text(json.dumps({"author": ROBO, "body": corpo}), encoding="utf-8")
            (pasta / "comentarios.json").write_text("[[]]", encoding="utf-8")
            (pasta / "par.txt").write_text("3 casos, 2 iguais, 1 s\nDIVERGE a\n", encoding="utf-8")
            args = ["texto", "--tipo", "issue-divergencia", "--skill", SKILL, "--estado", str(pasta / "art" / "estado.json"),
                    "--paridade", str(pasta / "par.txt"), "--codigo", "1", "--comentarios", str(pasta / "comentarios.json"),
                    "--corpo-atual", str(pasta / "corpo.json"), "--saida", str(pasta / "saida.md")]
            rodar_cli(args + ["--hoje", "2026-12-03"])
            self.assertFalse((pasta / "saida.md").exists())
            # corpo de outra pessoa não conta
            (pasta / "corpo.json").write_text(json.dumps({"author": GENTE, "body": corpo}), encoding="utf-8")
            rodar_cli(args + ["--hoje", "2026-12-03"])
            self.assertTrue((pasta / "saida.md").exists())

    def test_corpo_do_pr_de_regra_traz_a_marca_do_estado(self):
        texto = robo.texto_pr_regra(META, TextosTest.UP, CEM, "", hoje="2026-12-02")
        self.assertIn(robo.marca("regra-atualizada", TextosTest.UP, "2026-12-02"), texto)
        self.assertFalse(robo.deve_comentar([texto], "regra-atualizada", TextosTest.UP, "2026-12-03"))

    def test_versao_no_ar_so_do_corpo_do_robo(self):
        with tempfile.TemporaryDirectory() as pasta:
            pasta = Path(pasta)
            corpo = pasta / "corpo.json"
            corpo.write_text(json.dumps({"author": GENTE, "body": "<!-- cd2-robo no-ar=0.0.1 -->"}), encoding="utf-8")
            saida = pasta / "saida.md"
            rodar_cli(["texto", "--tipo", "issue-gpt", "--skill", SKILL, "--versao-nova", "0.6.1",
                       "--versao-anterior", "0.6.0", "--corpo-atual", str(corpo), "--saida", str(saida)])
            self.assertIn("no-ar=0.6.0", saida.read_text(encoding="utf-8"))
            corpo.write_text(json.dumps({"author": ROBO, "body": "<!-- cd2-robo no-ar=0.5.9 -->"}), encoding="utf-8")
            rodar_cli(["texto", "--tipo", "issue-gpt", "--skill", SKILL, "--versao-nova", "0.6.1",
                       "--versao-anterior", "0.6.0", "--corpo-atual", str(corpo), "--saida", str(saida)])
            self.assertIn("no-ar=0.5.9", saida.read_text(encoding="utf-8"))

    def test_login_do_robo_sem_is_bot_nao_conta(self):
        titulo_issue = robo.titulo("issue-gpt", SKILL)
        for autor in ({"login": "github-actions", "is_bot": False}, {"login": "github-actions"},
                      {"login": "app/github-actions", "is_bot": "true"}):
            self.assertIsNone(robo.issue_do_robo([{"number": 1, "title": titulo_issue, "author": autor}], titulo_issue), autor)
            self.assertIsNone(robo.pr_do_robo([pr(2, author=autor)], TITULO_PR, REPO), autor)
            self.assertEqual(robo.corpos_do_robo([{"author": autor, "body": "x"}]), [])

    def test_cli_achar(self):
        with tempfile.TemporaryDirectory() as pasta:
            lista = Path(pasta) / "prs.json"
            lista.write_text(json.dumps([pr(2, author=GENTE), pr(9)]), encoding="utf-8")
            _, _, linhas = rodar_cli_com_saida(["achar", "--tipo", "pr", "--de", "pr-regra", "--skill", SKILL,
                                                "--lista", str(lista), "--repo", REPO])
            self.assertEqual(linhas, ["numero=9"])
            lista.write_text(json.dumps([pr(2, author=GENTE)]), encoding="utf-8")
            _, _, linhas = rodar_cli_com_saida(["achar", "--tipo", "pr", "--de", "pr-regra", "--skill", SKILL,
                                                "--lista", str(lista), "--repo", REPO])
            self.assertEqual(linhas, ["numero="])


class Rodada2Test(unittest.TestCase):
    def test_texto_seguro_quebra_url_e_mencao(self):
        seguro = robo.texto_seguro("veja https://exemplo.invalido/x, http://a.b, www.c.d, @fulano e a@b.com")
        self.assertNotRegex(seguro, r"@[A-Za-z0-9]")
        self.assertNotIn("://", seguro)
        self.assertNotRegex(seguro, r"(?i)www\.")
        # continua legível: só caracteres de largura zero foram somados
        self.assertIn("fulano", seguro)
        self.assertIn("exemplo.invalido/x", seguro)
        self.assertIn("WWW\u200b.", robo.texto_seguro("WWW.exemplo"))
        texto = robo.texto_issue("issue-falha", skill=SKILL, meta=META, motivo="@alguem veja https://x.invalido")
        linha = next(l for l in texto.splitlines() if l.startswith("Motivo:"))
        self.assertNotRegex(linha, r"@[A-Za-z0-9]")
        self.assertNotIn("://", linha)

    def test_paridade_com_numero_absurdo_e_none(self):
        self.assertIsNone(robo.ler_paridade("1" * 5000 + " casos, 1 iguais, 1 s\n", 0))
        self.assertIsNone(robo.ler_paridade("99999999999999999999 casos, 99999999999999999999 iguais, 1 s\n", 0))
        self.assertIsNone(robo.ler_paridade(f"{robo.MAX_CASOS + 1} casos, {robo.MAX_CASOS + 1} iguais, 1 s\n", 0))
        self.assertEqual(robo.ler_paridade(f"{robo.MAX_CASOS} casos, {robo.MAX_CASOS} iguais, 1 s\n", 0)["casos"],
                         robo.MAX_CASOS)

    def test_caminhos_com_acento_e_quebra_de_linha(self):
        bruto = "src/main/java/br/Ação.java\0src/main/x\ny.java\0flyway/sql/manutencao/V0074__açaí.sql\0".encode("utf-8")
        self.assertEqual(robo.ler_caminhos(bruto),
                         ["src/main/java/br/Ação.java", "src/main/x\ny.java", "flyway/sql/manutencao/V0074__açaí.sql"])

    def test_detectar_le_o_diff_com_nul(self):
        with tempfile.TemporaryDirectory() as pasta:
            pasta = Path(pasta)
            for so_um in ("src/main/java/br/Ação.java", "src/main/x\ny.java"):
                (pasta / "caminhos").write_bytes(f"pom.xml\0{so_um}\0".encode("utf-8"))
                (pasta / "up.json").write_text(json.dumps(upstream(versao="1.0.3", commit_calc="b" * 40)), encoding="utf-8")
                codigo, _, linhas = rodar_cli_com_saida(
                    ["detectar", "--skill", SKILL, "--upstream", str(pasta / "up.json"),
                     "--caminhos", str(pasta / "caminhos"), "--saida", str(pasta / "estado.json")])
                self.assertEqual(codigo, 0)
                self.assertIn("classe=regra", linhas, so_um)

    def _raiz_com_meta(self, pasta, meta):
        raiz = pasta / "raiz"
        (raiz / "conferencia" / SKILL).mkdir(parents=True)
        shutil.copy2(ROOT / "conferencia" / SKILL / "config.json", raiz / "conferencia" / SKILL / "config.json")
        (raiz / "conferencia" / SKILL / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        return raiz

    def test_main_andou_no_meio_termina_sem_issue(self):
        with tempfile.TemporaryDirectory() as pasta:
            pasta = Path(pasta)
            _artefato(pasta / "art")  # estado.meta = META, o meta do commit do começo
            agora = json.loads(json.dumps(META))
            agora["calculadora"]["versao"] = "1.0.3"  # alguém fez merge de um PR de regra no meio
            raiz = self._raiz_com_meta(pasta, agora)
            comeco = pasta / "meta-do-comeco.json"
            comeco.write_text(json.dumps(META), encoding="utf-8")
            codigo, _, linhas = rodar_cli_com_saida(
                ["--raiz", str(raiz), "reconferir", "--skill", SKILL, "--artefato", str(pasta / "art"),
                 "--tag", "1.0.3", "--commit-calc", "c" * 40, "--commit-migr", "d" * 40,
                 "--meta-do-comeco", str(comeco), "--motivo-saida", str(pasta / "motivo")])
            self.assertEqual((codigo, linhas), (0, ["situacao=main-andou"]))
            codigo, _, linhas = rodar_cli_com_saida(
                ["--raiz", str(raiz), "decidir", "--skill", SKILL, "--estado", str(pasta / "art" / "estado.json"),
                 "--paridade", str(pasta / "art" / "paridade.txt"), "--codigo", "0",
                 "--resultado-conferencia", "success", "--ramo", "refs/heads/main"])
            self.assertEqual(linhas, ["acao=terminar", "pr="])

    def test_meta_que_nao_e_nem_o_de_agora_nem_o_do_comeco_e_falha(self):
        with tempfile.TemporaryDirectory() as pasta:
            pasta = Path(pasta)
            _artefato(pasta / "art")
            agora = json.loads(json.dumps(META))
            agora["calculadora"]["versao"] = "1.0.3"
            raiz = self._raiz_com_meta(pasta, agora)
            comeco = json.loads(json.dumps(META))
            comeco["base"]["data"] = "2001-01-01"
            (pasta / "comeco.json").write_text(json.dumps(comeco), encoding="utf-8")
            motivo = pasta / "motivo"
            codigo, _, linhas = rodar_cli_com_saida(
                ["--raiz", str(raiz), "reconferir", "--skill", SKILL, "--artefato", str(pasta / "art"),
                 "--tag", "1.0.2", "--commit-calc", "c" * 40, "--commit-migr", "d" * 40,
                 "--meta-do-comeco", str(pasta / "comeco.json"), "--motivo-saida", str(motivo)])
            self.assertEqual((codigo, linhas), (1, []))
            self.assertIn("nem o do repositório nem o do commit do começo", motivo.read_text(encoding="utf-8"))
            # sem o meta do começo, também é falha
            codigo, _, _ = rodar_cli_com_saida(
                ["--raiz", str(raiz), "reconferir", "--skill", SKILL, "--artefato", str(pasta / "art"),
                 "--tag", "1.0.2", "--commit-calc", "c" * 40, "--commit-migr", "d" * 40, "--motivo-saida", str(motivo)])
            self.assertEqual(codigo, 1)

    def test_decidir_recusa_estado_de_outra_skill(self):
        with tempfile.TemporaryDirectory() as pasta:
            pasta = Path(pasta)
            _artefato(pasta / "art")
            codigo, _, linhas = rodar_cli_com_saida(
                ["decidir", "--skill", "outra-skill", "--estado", str(pasta / "art" / "estado.json"),
                 "--resultado-conferencia", "success", "--ramo", "refs/heads/main"])
            self.assertEqual(linhas, ["acao=issue-falha", "pr="])


class ReleaseTest(unittest.TestCase):
    def test_dispara_quando_falta_a_release_e_nada_esta_rodando(self):
        releases = [{"tagName": "cd2-super-v0.6.0"}]
        self.assertTrue(robo.deve_disparar_release("0.6.1", releases, [{"status": "completed"}]))
        self.assertFalse(robo.deve_disparar_release("0.6.0", releases, []))
        self.assertFalse(robo.deve_disparar_release("0.6.1", releases, [{"status": "in_progress"}]))
        self.assertFalse(robo.deve_disparar_release("0.6.1", releases, [{"status": "queued"}]))
        with self.assertRaises(ValueError):
            robo.deve_disparar_release("0.6.1\n", releases, [])


class TextoPrReleaseSemCodigoTest(unittest.TestCase):
    def test_nao_diz_que_o_codigo_de_calculo_mudou(self):
        up = TextosTest.UP
        texto = robo.texto_pr_regra(META, up, MENOS, "", classe="release-sem-codigo")
        self.assertNotIn("mudou o código de cálculo", texto)
        self.assertIn("sem mudança no código de cálculo", texto)
        self.assertTrue(texto.startswith("**As tabelas"))
        self.assertIn("mudou o código de cálculo", robo.texto_pr_regra(META, up, MENOS, ""))


if __name__ == "__main__":
    unittest.main()
