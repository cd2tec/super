import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WF = ROOT / ".github" / "workflows"


def wf(name):
    return (WF / name).read_text(encoding="utf-8")


def _bloco(linhas, i, recuo):
    """Linhas depois de i com recuo maior que `recuo` (o corpo de um escalar de bloco do YAML)."""
    corpo = []
    for linha in linhas[i + 1:]:
        if linha.strip() and len(linha) - len(linha.lstrip(" ")) <= recuo:
            break
        corpo.append(linha)
    return "\n".join(corpo)


def run_blocks(text):
    """O texto de cada `run:` do workflow, em bloco (`run: |`) ou numa linha só. Sem PyYAML."""
    linhas = text.splitlines()
    blocos = []
    for i, linha in enumerate(linhas):
        achou = re.match(r"^(\s*)(?:- )?run:\s*(.*)$", linha)
        if not achou:
            continue
        recuo = len(achou.group(1))
        resto = achou.group(2)
        blocos.append(_bloco(linhas, i, recuo) if resto in ("|", "|-", ">", ">-") else resto)
    return blocos


def steps(text):
    """(job, if, texto) de cada passo: o passo começa num `- ` com seis espaços de recuo."""
    linhas = text.splitlines()
    saida, job, atual = [], None, None
    for i, linha in enumerate(linhas):
        job_achado = re.match(r"^  ([a-z][a-z0-9_-]*):\s*$", linha)
        if job_achado:
            job = job_achado.group(1)
        if re.match(r"^      - ", linha):
            atual = {"job": job, "linhas": []}
            saida.append(atual)
        elif atual is not None and linha.strip() and len(linha) - len(linha.lstrip(" ")) <= 4:
            atual = None
        if atual is not None:
            atual["linhas"].append(linha)
    resultado = []
    for passo in saida:
        corpo = passo["linhas"]
        condicao = ""
        for j, linha in enumerate(corpo):
            achou = re.match(r"^(\s*)(?:- )?if:\s*(.*)$", linha)
            if achou:
                condicao = _bloco(corpo, j, len(achou.group(1))) if achou.group(2) in (">-", ">", "|") else achou.group(2)
                break
        resultado.append((passo["job"], condicao, "\n".join(corpo)))
    return resultado


# Expressões ${{ }} que podem ficar dentro de um run: nenhuma. Tudo entra por env: e entre aspas.
EXPRESSOES_PERMITIDAS_NO_RUN: tuple = ()


class WorkflowTest(unittest.TestCase):
    def test_four_workflows_exist(self):
        self.assertEqual(sorted(p.name for p in WF.glob("*.yml")),
                         ["check-sync.yml", "conferencia-receita.yml", "pages.yml", "release.yml"])

    def test_conferencia_da_receita(self):
        text = wf("conferencia-receita.yml")
        for needle in (
            "cron:",
            "workflow_dispatch:",
            "eclipse-temurin-25@sha256:",
            "receita_paridade.py",
            "--completa",
            "receita_robo.py",
            "receita_base.py",
            "sync_plugins.py --check",
            "unittest discover",
            "CD2_BASE_COMPLETA",
            "gh workflow run release.yml",
            "gh workflow enable",
            "group: conferencia-receita",
            "cancel-in-progress: false",
            "persist-credentials: false",
        ):
            self.assertIn(needle, text)
        self.assertIn('cron: "17 9 * * *"', text)
        # Nenhum segredo além do token do próprio repositório.
        self.assertNotIn("secrets.", text.replace("secrets.GITHUB_TOKEN", ""))
        # Nunca reescreve história nem força push.
        for proibido in ("--force", "push -f", "reset --hard", "rebase"):
            self.assertNotIn(proibido, text)
        # Permissões de escrita só no job que não roda código da Receita.
        self.assertRegex(text, r"(?m)^permissions:\n  contents: read\n")
        for perm in ("contents: write", "pull-requests: write", "issues: write", "actions: write"):
            self.assertIn(perm, text)
        # O teste completo roda antes do commit e o commit antes do push.
        self.assertLess(text.index("sync_plugins.py --check"), text.index("git commit"))
        self.assertLess(text.index("unittest discover"), text.index("git commit"))
        self.assertLess(text.index("git commit"), text.index("git push origin HEAD:main"))
        # O robô não é o Claude: commit do robô sem linha de coautoria.
        self.assertNotIn("Co-Authored-By", text)

    def test_nenhuma_expressao_dentro_de_run(self):
        for path in WF.glob("*.yml"):
            for bloco in run_blocks(path.read_text(encoding="utf-8")):
                for expressao in re.findall(r"\$\{\{[^}]*\}\}", bloco):
                    self.assertIn(expressao, EXPRESSOES_PERMITIDAS_NO_RUN, f"{path.name}: {expressao}")
                self.assertNotIn("${{", bloco, path.name)

    def test_parser_de_run_acha_os_blocos(self):
        blocos = run_blocks(wf("conferencia-receita.yml"))
        self.assertGreater(len(blocos), 15)
        self.assertTrue(any("gh workflow enable" in b for b in blocos))
        self.assertEqual(run_blocks("    steps:\n      - run: echo ${{ x }}\n"), ["echo ${{ x }}"])

    def test_codigo_da_receita_so_em_conteiner_isolado(self):
        text = wf("conferencia-receita.yml")
        blocos = run_blocks(text)
        # mvn e java só como comando dentro do docker run
        for bloco in blocos:
            for linha in bloco.splitlines():
                if re.search(r"(^|\s)(mvn|java)\s", linha) and "docker run" not in bloco:
                    self.fail(f"código da Receita fora de contêiner: {linha.strip()}")
        montagens = [m.split(":")[0] for m in re.findall(r'-v "([^"]+)"', text)]
        self.assertTrue(montagens)
        for origem in montagens:
            self.assertTrue(origem.startswith("$ISOL/") or origem in ("$JAR", "$BANCO"), origem)
        self.assertIn("ISOL: /tmp/receita-isolada", text)
        self.assertIn(":/app/calculadora/db/calculadora-pro.db:ro", text)
        self.assertIn("--network receita-oficial", text)
        self.assertIn("DOCKER-USER -i br-receita -j DROP", text)
        self.assertIn('-p "127.0.0.1:$PORTA:$PORTA"', text)
        # contêineres desligados antes de a pasta dos resultados receber arquivos
        self.assertLess(text.index("name: Desligar os contêineres"), text.index("name: Guardar o que o job agir precisa"))

    def test_produto_do_build_so_passa_se_for_arquivo_comum_da_pasta(self):
        # Docker segue link simbólico na origem de um -v, e o cp também: nada do build é usado sem conferir
        for bloco in run_blocks(wf("conferencia-receita.yml")):
            usa = re.findall(r'"\$ISOL/produtos/[^"$]+"', bloco)
            self.assertEqual(usa, [], f"produto do build usado sem passar pela conferência: {usa}")
            if "$(produto " in bloco:
                self.assertIn("produto() {", bloco)
                self.assertIn('[ -L "$p" ]', bloco)
                self.assertIn('[ ! -f "$p" ]', bloco)
                self.assertIn('realpath "$ISOL/produtos"', bloco)
            for variavel in ("JAR", "BANCO"):
                if f'"${variavel}' in bloco or f'"${variavel}:' in bloco:
                    self.assertRegex(bloco, rf"{variavel}=\$\(produto [a-z.-]+\)")
        text = wf("conferencia-receita.yml")
        self.assertEqual(text.count("-c core.symlinks=false"), 2)
        self.assertEqual(len(re.findall(r"diff -z --no-renames --name-only", text)), 2)
        self.assertNotRegex(text, r"diff --no-renames --name-only")

    def test_comentarios_so_pela_api_rest(self):
        # o gh --json comments não diz se o autor do comentário é bot: o robô se perderia e comentaria todo dia
        text = wf("conferencia-receita.yml")
        self.assertNotIn("--json comments", text)
        leituras = re.findall(r'gh api "repos/\$GITHUB_REPOSITORY/issues/\$[A-Za-z]+/comments" --paginate --slurp', text)
        self.assertEqual(len(leituras), 5)
        # o corpo de antes é lido antes de o PR ser editado
        passo = next(c for j, _, c in steps(text) if "name: Abrir ou atualizar o PR de regra" in c)
        self.assertLess(passo.index('gh pr view "$PR" --json body,author'), passo.index('gh pr edit "$PR"'))

    def test_agir_le_o_meta_do_commit_do_comeco(self):
        text = wf("conferencia-receita.yml")
        agir = text[text.index("\n  agir:"):text.index("\n  manter:")]
        self.assertIn("fetch-depth: 0", agir)
        self.assertIn('git show "$GITHUB_SHA:conferencia/$SKILL/meta.json"', agir)
        self.assertIn("--meta-do-comeco", agir)
        self.assertIn('decidir --skill "$SKILL"', agir)

    def test_fora_da_main_nada_escreve(self):
        escreve = re.compile(r"gh (issue|pr) (create|edit|comment)|git push|gh workflow (run|enable)|gh api -X PUT")
        achados = 0
        for job, condicao, corpo in steps(wf("conferencia-receita.yml")):
            if escreve.search(corpo):
                achados += 1
                self.assertIn("github.ref == 'refs/heads/main'", condicao, f"{job}: {corpo.splitlines()[0]}")
        self.assertGreaterEqual(achados, 8)

    def test_divergencia_da_paridade_nao_derruba_o_passo(self):
        # o shell do GitHub roda com -e: o código 1 da paridade tem de chegar ao decidir
        passo = next(c for j, _, c in steps(wf("conferencia-receita.yml")) if "name: Conferência completa" in c)
        self.assertIn("set +e", passo)
        self.assertLess(passo.index("set +e"), passo.index("receita_paridade.py"))

    def test_agir_reconfere_antes_de_decidir(self):
        text = wf("conferencia-receita.yml")
        agir = text[text.index("\n  agir:"):text.index("\n  manter:")]
        self.assertLess(agir.index("receita_robo.py reconferir"), agir.index("receita_robo.py decidir"))
        self.assertIn("git ls-remote", agir)
        self.assertIn("/compare/", agir)
        self.assertNotIn("docker", agir)
        self.assertNotIn("mvn", agir)

    def test_check_sync_runs_sync_tests_and_json(self):
        text = wf("check-sync.yml")
        for needle in (
            "pull_request:",
            "branches: [main]",
            "python-version: \"3.12\"",
            "sync_plugins.py --check",
            "unittest discover -s tests -v",
            "json.tool",
            "gpts/*/gpt.json",
            "core/*/plugin.core.json",
            ".agents/plugins/marketplace.json",
            ".claude-plugin/marketplace.json",
            "harnesses.json",
            "skills.json",
        ):
            self.assertIn(needle, text)

    def test_pages_publishes_docs(self):
        text = wf("pages.yml")
        for needle in ("path: docs", '"docs/**"', "workflow_dispatch:", "actions/deploy-pages@v4", "actions/upload-pages-artifact@v3", "actions/configure-pages@v5"):
            self.assertIn(needle, text)

    def test_release_checks_packages_and_tags(self):
        text = wf("release.yml")
        for needle in (
            "sync_plugins.py --check",
            "package_plugins.py --out dist",
            '"core/*/plugin.core.json"',
            "workflow_dispatch:",
            "gh release create",
            "gh release upload",
            'dist/"$plugin"-*.zip',
        ):
            self.assertIn(needle, text)
        # A tag cd2-super-v<versão> sai de <plugin>-v<versão> (package_plugins.py).
        self.assertIn("-v", text)
        self.assertLess(text.index("sync_plugins.py --check"), text.index("package_plugins.py"))

    def test_release_notes_name_every_platform_and_zip(self):
        text = wf("release.yml")
        for needle in ("Claude Cowork", "Codex e ChatGPT Work", "DeepSeek Harness", "GPTs para a GPT Store"):
            self.assertIn(needle, text)
        for pkg in ("claude", "codex", "dsh", "gpts"):
            self.assertIn(f"$plugin-{pkg}-$version.zip", text)
            self.assertIn(f"$plugin-{pkg}.zip", text)

    def test_tag_prefix_matches_package_script(self):
        script = (ROOT / "scripts" / "package_plugins.py").read_text(encoding="utf-8")
        self.assertIn('f"{plugin}-v{version}"', script)
        self.assertIn("cd2-super", (ROOT / "harnesses.json").read_text(encoding="utf-8"))
        self.assertIn("cd2-super-v", "cd2-super-v0.2.0")

    def test_actions_are_pinned_and_text_is_clean(self):
        for path in WF.glob("*.yml"):
            text = path.read_text(encoding="utf-8")
            for use in re.findall(r"uses:\s*(\S+)", text):
                self.assertRegex(use, r"@v\d+$", f"{path.name}: {use}")
            self.assertNotRegex(text, "[—–]", path.name)
            self.assertNotRegex(text, "[\U0001F300-\U0001FAFF☀-➿]", path.name)


if __name__ == "__main__":
    unittest.main()
