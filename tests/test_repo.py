import codecs
import json
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Nada disto pode aparecer num arquivo público: nome de pessoa, caminho local,
# ferramenta interna ou repositório privado das calculadoras.
# Os nomes próprios ficam em rot13 para este arquivo não carregar o que ele procura.
LEAKS = tuple(codecs.decode(s, "rot13") for s in (
    "Tnoevry", "anlnovat", "sevqnl", "pq2grp/pq2-pnyp", "nepurb-unearff-cyhtvaf",
    "nanylmr-zpc", "ohvyq-zpc-qnfuobneq", "fcrp-zpc-gbby", "Ergnvy Grpu",
)) + (
    "/private/tmp", "scratchpad", "claude.ai/artifact", "docs/superpowers")
NOMES_ANTIGOS = tuple(codecs.decode(s, "rot13") for s in (
    "nepurb-unearff", "nanylmr-zpc", "ohvyq-zpc-qnfuobneq", "fcrp-zpc-gbby", "Ergnvy Grpu"))
PERSON = codecs.decode("Tnoevry", "rot13")
INTERNAL_DIRS = ("interno/", ".superpowers/", "dist/")


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def tracked_files():
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        raise unittest.SkipTest("git indisponível")
    return [p for p in out.stdout.decode("utf-8").split("\0") if p]


def tracked_texts():
    """(caminho, texto) de cada arquivo de texto versionado, menos os testes."""
    for rel in tracked_files():
        if rel.startswith("tests/") or not (ROOT / rel).is_file():
            continue
        try:
            yield rel, (ROOT / rel).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue


LICENSE_90 = re.compile(
    r"livre por até 90 dias|por até 90 dias, (só )?para avaliar|até 90 dias, para avaliar"
    r"|avaliação grátis por 90|90 dias de avaliação|stat\">90 dias"
)


class RepoTest(unittest.TestCase):
    def test_license_is_official_cc_by_nc_nd(self):
        self.assertIn("Attribution-NonCommercial-NoDerivatives 4.0 International", read("LICENSE"))

    def test_notice_credits_cd2_tech_only(self):
        text = read("NOTICE")
        self.assertIn("CD2 Tech", text)
        self.assertNotIn(PERSON, text)

    def test_no_leaks_in_any_tracked_file(self):
        for rel, text in tracked_texts():
            lowered = text.lower()
            for needle in LEAKS:
                self.assertFalse(needle.lower() in lowered, f"{rel}: {needle}")

    def test_internal_dirs_are_ignored_and_untracked(self):
        ignore = read(".gitignore").split()
        for folder in INTERNAL_DIRS:
            self.assertIn(folder, ignore)
        for rel in tracked_files():
            self.assertFalse(rel.startswith(INTERNAL_DIRS + ("docs/superpowers/",)), rel)

    def test_no_deliverable_says_use_as_much_as_you_want(self):
        # O teste na empresa é livre e sem prazo (COMMERCIAL.md): nenhum texto pode dizer o contrário.
        for rel, text in tracked_texts():
            self.assertFalse("quanto quiser" in text, rel)

    def test_notice_states_font_exception(self):
        text = read("NOTICE")
        self.assertIn("SIL Open Font License 1.1", text)
        self.assertIn("não é coberta pela licença Creative Commons", text)

    def test_commercial_testar_livre(self):
        text = read("COMMERCIAL.md")
        self.assertIn("para testar, livremente e sem prazo", text)
        for needle in ("cliente", "wa.me/5519982355172"):
            self.assertIn(needle, text)

    def test_licenca_sem_prazo(self):
        for rel, text in tracked_texts():
            if rel == "CHANGELOG.md":
                continue  # histórico da 0.1.0 continua verdadeiro
            self.assertIsNone(LICENSE_90.search(text), rel)

    def test_testes_tambem_sem_nomes_antigos(self):
        # tracked_texts() pula tests/: este teste cobre os outros arquivos de teste. Só o test_repo.py
        # os cita (em rot13).
        for path in sorted((ROOT / "tests").glob("*.py")):
            if path.name == "test_repo.py":
                continue
            lowered = path.read_text(encoding="utf-8").lower()
            for needle in NOMES_ANTIGOS:
                self.assertNotIn(needle.lower(), lowered, f"{path.name}: {needle}")

    def test_sem_apache(self):
        # A licença é só a CC BY-NC-ND 4.0. A Manrope (assets/fonts) segue na OFL e fica de fora.
        for rel, text in tracked_texts():
            if "/fonts/" in rel or rel.endswith("OFL.txt"):
                continue
            self.assertNotIn("apache", text.lower(), rel)

    def test_json_files_parse(self):
        core = json.loads(read("core/cd2-super/plugin.core.json"))
        skills = json.loads(read("skills.json"))
        harnesses = json.loads(read("harnesses.json"))
        # A versão sobe a cada publicação (à mão ou pelo robô): o que vale é a entrada do CHANGELOG.md no topo.
        self.assertRegex(core["version"], r"^\d+\.\d+\.\d+$")
        self.assertEqual(re.search(r"(?m)^## (\S+)$", read("CHANGELOG.md")).group(1), core["version"])
        self.assertEqual(
            {s["name"] for s in skills},
            {
                "fim-escala-6x1", "custo-vrva", "cbs-ibs-simples", "cbs-ibs-regime-regular", "relatorio-vendas",
                "relatorio-estoque",
                "relatorio-compras", "relatorio-precos", "relatorio-perdas", "relatorio-financeiro",
                "relatorio-fiscal", "relatorio-contabil", "painel-varejo", "ficha-archeo-mcp",
                "perfil-da-operacao", "briefing-do-dia", "sinais-da-operacao", "preparo-de-negociacao", "ata-de-negociacao", "plano-de-ofertas", "comunicacao-de-ofertas",
                "fechamento-do-dia", "resumo-da-semana", "ficha-do-fornecedor", "cenario-de-custo",
                "resultado-da-oferta", "revisao-de-oferta", "radar-de-concorrencia", "visita-de-loja",
            },
        )
        self.assertEqual(set(harnesses["plugins"]["cd2-super"]["harnesses"]), {"claude", "codex", "dsh", "gpt"})


class AtribuicaoReceita(unittest.TestCase):
    """A skill cbs-ibs-simples porta código e tabelas da Receita Federal, sob licença MIT."""

    SN = "https://github.com/ReceitaFederal/calculadora-rtc-simples-nacional"
    RR = "https://github.com/ReceitaFederal/calculadora-rtc-regime-regular"

    def test_notice_cita_os_dois_repositorios_e_a_mit(self):
        text = read("NOTICE")
        for needle in (self.SN, self.RR, "cbs-ibs-simples", "MIT License",
                       "Copyright (c) 2026 Receita Federal do Brasil",
                       "Permission is hereby granted, free of charge, to any person obtaining a copy",
                       'THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND'):
            self.assertIn(needle, text, needle)
        # Os parágrafos de antes continuam lá.
        for needle in ("Copyright (c) 2026 CD2 Tech", "SIL Open Font License 1.1", "Condições adicionais de uso"):
            self.assertIn(needle, text, needle)

    def test_skill_e_gpt_dizem_que_nao_sao_da_receita(self):
        for rel in ("core/cd2-super/skills/cbs-ibs-simples/SKILL.md", "core/cd2-super/gpts/cbs-ibs-simples/instructions.md",
                    "core/cd2-super/skills/cbs-ibs-regime-regular/SKILL.md",
                    "core/cd2-super/gpts/cbs-ibs-regime-regular/instructions.md"):
            self.assertIn("não é um aplicativo da Receita Federal", read(rel), rel)

    def test_skill_cobre_seletivo_desconhecido_nfse_e_entrada(self):
        skill = read("core/cd2-super/skills/cbs-ibs-simples/SKILL.md")
        gpt = read("core/cd2-super/gpts/cbs-ibs-simples/instructions.md")
        for texto in (skill, gpt):
            self.assertIn("O total não inclui o Imposto Seletivo do item {n} ({descrição}). "
                          "A alíquota ainda não foi definida em lei.", texto)
            self.assertIn("observacoes", texto)
            self.assertIn("nunca conselho", texto)
            self.assertIn("sem % nem R$", texto)
            self.assertIn("nunca escolh", texto)
        self.assertIn("Se a pessoa não souber o valor", skill)
        self.assertIn("Na NFS-e, ou se `sugestoes` vier vazia", skill)
        self.assertIn('`{"errors": [...]}`, com código de saída 2', skill)
        self.assertIn("Onde o texto acima difere, vale esta seção", gpt)

    def test_skill_json_da_skill(self):
        item = next(s for s in json.loads(read("skills.json")) if s["name"] == "cbs-ibs-simples")
        self.assertEqual(item, {
            "name": "cbs-ibs-simples", "title": "CBS e IBS no Simples Nacional",
            "summary": "Calcula a CBS e o IBS de uma operação do Simples Nacional com a mesma conta da calculadora "
                       "oficial da Receita Federal.",
            "site_url": self.SN, "rule": "Lei Complementar 214/2025", "status": "disponível",
            "tipo": "calculadora", "gpt": True, "departamento": "Fiscal", "fonte_externa": True,
        })


class SkillRegimeRegular(unittest.TestCase):
    """A skill cbs-ibs-regime-regular porta a calculadora oficial do regime regular da Receita Federal."""

    RR = "https://github.com/ReceitaFederal/calculadora-rtc-regime-regular"
    SKILL = "core/cd2-super/skills/cbs-ibs-regime-regular/SKILL.md"
    GPT = "core/cd2-super/gpts/cbs-ibs-regime-regular/instructions.md"
    CALC_IMPOSTO = ("https://calc-imposto.cd2.com.br/?utm_source={{UTM_SOURCE}}&utm_medium=skill"
                    "&utm_campaign=cbs-ibs-regime-regular")

    def test_skill_json_da_skill(self):
        skills = json.loads(read("skills.json"))
        item = next(s for s in skills if s["name"] == "cbs-ibs-regime-regular")
        simples = next(s for s in skills if s["name"] == "cbs-ibs-simples")
        self.assertEqual(item["site_url"], self.RR)
        self.assertEqual((item["tipo"], item["gpt"], item["fonte_externa"]), ("calculadora", True, True))
        self.assertEqual(item["departamento"], simples["departamento"])
        self.assertEqual(item["rule"], "Lei Complementar 214/2025")

    def test_description_com_as_palavras_da_pessoa(self):
        texto = read(self.SKILL)
        description = texto.split("description:", 1)[1].split("\n", 1)[0]
        for palavra in ("Lucro Real", "Lucro Presumido", "regime regular", "Imposto Seletivo", "CBS", "IBS",
                        "cClassTrib", "JSON da calculadora da Receita", "Simples", "NFS-e", "emitir"):
            self.assertIn(palavra, description, palavra)

    def test_simples_aponta_para_o_regime_regular(self):
        texto = read("core/cd2-super/skills/cbs-ibs-simples/SKILL.md")
        description = texto.split("description:", 1)[1].split("\n", 1)[0]
        self.assertIn("cbs-ibs-regime-regular", description)

    def test_conversa_e_gpt(self):
        skill, gpt = read(self.SKILL), read(self.GPT)
        for texto in (skill, gpt):
            for needle in ("2027-01-01", "em-2027", "reforma-completa", "hipótese da CD2", "simulad",
                           "REG-036", "REG-037", "REG-013", "sugestao.py", "--checar-versao", "render.py",
                           "não é um aplicativo da Receita Federal", self.CALC_IMPOSTO,
                           "NFS-e", "gCompraGov", "Lucro Real", "Presumido"):
                self.assertIn(needle, texto, needle)
            # A redução vale no regime regular: a frase do Simples não vem para cá.
            self.assertNotIn("não baixa a CBS", texto)
            self.assertNotIn("sem efeito", texto)
            self.assertNotIn("PGDAS", texto)
        self.assertIn("cbs-ibs-simples", skill)
        self.assertIn("CD2 CBS e IBS no Simples", gpt)
        self.assertLessEqual(len(gpt), 8000)
        self.assertIn("01/01/2027", skill)
        self.assertIn("7 dígitos", skill)

    def test_metodologia(self):
        texto = read("core/cd2-super/skills/cbs-ibs-regime-regular/references/metodologia.md")
        config = json.loads(read("conferencia/cbs-ibs-regime-regular/config.json"))
        meta = json.loads(read("conferencia/cbs-ibs-regime-regular/meta.json"))
        for needle in (meta["calculadora"]["versao"], meta["base"]["versao"], "ignorar", "timestamp",
                       "hipótese da CD2", "Em 2027", "Reforma completa", "Bean Validation", "CAL-007",
                       "notação científica", "parallelStream", "0000", "excluidos_da_bateria"):
            self.assertIn(needle, texto, needle)
        self.assertIn(f"{meta['conferencia']['casos']:,}".replace(",", "."), texto)

    def test_arquivos_motor_lista_os_modulos(self):
        config = json.loads(read("conferencia/cbs-ibs-regime-regular/config.json"))
        scripts = ROOT / "core/cd2-super/skills/cbs-ibs-regime-regular/scripts"
        modulos = sorted(p.name for p in scripts.glob("rr_*.py")) + ["numeros.py"]
        self.assertEqual(sorted(config["arquivos_motor"]), sorted(modulos))


class Departamentos(unittest.TestCase):
    def test_departamentos_pedidos_tem_skill(self):
        skills = json.loads((ROOT / "skills.json").read_text(encoding="utf-8"))
        usados = {s["departamento"] for s in skills}
        self.assertLessEqual({"Comercial", "Compras", "Financeiro", "RH", "Loja", "Fiscal", "Contábil"}, usados)


class ArcheoPreConfigurado(unittest.TestCase):
    MCP = ROOT / "plugins/claude/cd2-super/.mcp.json"

    def test_mcp_json_archeo(self):
        texto = self.MCP.read_text(encoding="utf-8")
        servidor = json.loads(texto)["mcpServers"]["archeo"]
        self.assertEqual(servidor, {"type": "http", "url": "https://archeo.cd2.com.br/api/mcp"})
        self.assertNotIn("authorization", texto.lower())
        self.assertNotIn("token", texto.lower())


if __name__ == "__main__":
    unittest.main()
