#!/usr/bin/env python3
"""Robô de conferência com a Receita Federal: decide o que fazer quando a Receita muda.

Roda no workflow `.github/workflows/conferencia-receita.yml`, uma vez por skill de `conferencia/`.
Os subcomandos conversam por arquivos JSON e, no GitHub Actions, por `$GITHUB_OUTPUT`:

    detectar        compara a Receita (upstream.json) com o meta.json da skill e diz a classe da mudança
    comparar-bases  confere que a base do Flyway oficial e a do receita_base.py têm o mesmo conteúdo
    esperar-oficial espera a calculadora oficial responder /versao com a versão e a base esperadas
    decidir         junta a classe, a conferência e o PR de regra aberto e diz a ação
    publicar        grava a meta nova, sobe a versão (patch) e escreve o CHANGELOG.md
    meta            copia o meta.json para a CD2_META da base e para o topo do esperadas.json
    texto           escreve o corpo de PR, issue ou comentário

Nada aqui fala com a rede além do endereço local da calculadora oficial: quem conversa com o
GitHub é o workflow, pelo `gh`. Nenhum dado de ninguém sai daqui.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

import receita_base  # noqa: E402
import receita_paridade  # noqa: E402

MANIFESTOS = [
    "core/cd2-super/plugin.core.json",
    "plugins/claude/cd2-super/.claude-plugin/plugin.json",
    "plugins/codex/cd2-super/plugin.json",
    "plugins/dsh/cd2-super/plugin.json",
]
CLASSES = ("nada", "tabelas", "release-sem-codigo", "regra")
ACOES = ("terminar", "publicar", "issue-divergencia", "pr-regra", "congelado", "issue-falha")
# O GitHub recusa corpo de PR ou issue com mais de 65.536 caracteres; sobra folga para o resto.
LIMITE_CORPO = 60000
MAX_DIVERGENTES = 50
_LINHA_PARIDADE = re.compile(r"^(\d+) casos, (\d+) iguais, ", re.MULTILINE)
_DIVERGE = re.compile(r"^DIVERGE (\S+)", re.MULTILINE)
_SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
_NO_AR = re.compile(r"<!-- cd2-robo no-ar=(\S+) -->")
_MARCA = re.compile(r"<!-- cd2-robo tipo=(\S+) estado=(\S+) dia=(\S+) -->")


# --- decisão ---------------------------------------------------------------------

def _sob(caminho: str, prefixo: str) -> bool:
    prefixo = prefixo.strip("/")
    return caminho == prefixo or caminho.startswith(prefixo + "/")


def classificar(meta: dict, upstream: dict, caminhos_mudados: list[str], config: dict) -> str:
    """"nada", "tabelas", "release-sem-codigo" ou "regra".

    `caminhos_mudados` traz os caminhos do repositório da calculadora entre o commit espelhado e o
    da release nova, mais os caminhos das migrações (já dentro de `config["migracoes"]["caminho"]`)
    entre o commit espelhado e o novo."""
    codigo = config["calculadora"]["caminho_codigo"]
    migracoes = config["migracoes"]["caminho"]
    if upstream["calculadora"]["versao"] != meta["calculadora"]["versao"]:
        return "regra" if any(_sob(c, codigo) for c in caminhos_mudados) else "release-sem-codigo"
    if (upstream["migracoes"]["commit"] != meta["migracoes"]["commit"]
            and any(_sob(c, migracoes) for c in caminhos_mudados)):
        return "tabelas"
    return "nada"


def _cem_por_cento(paridade: dict) -> bool:
    return paridade["casos"] > 0 and paridade["iguais"] == paridade["casos"]


def decidir(classe: str, paridade: dict | None, pr_de_regra_aberto: bool) -> str:
    """A tabela de decisão da seção 6.4 do spec. Na dúvida, não publica."""
    if classe not in CLASSES:
        raise ValueError(f"classe desconhecida: {classe}")
    if classe == "nada":
        return "congelado" if pr_de_regra_aberto else "terminar"
    if paridade is None or paridade.get("casos", 0) <= 0:
        return "issue-falha"
    if classe == "regra":
        return "pr-regra"
    if pr_de_regra_aberto:
        return "congelado"
    if _cem_por_cento(paridade):
        return "publicar"
    return "issue-divergencia" if classe == "tabelas" else "pr-regra"


def ler_paridade(texto: str, codigo: int) -> dict | None:
    """Lê a saída do receita_paridade.py. None quando a conferência não chegou ao fim ou quando
    a saída e o código de saída se contradizem."""
    achou = _LINHA_PARIDADE.search(texto or "")
    if achou is None or codigo not in (0, 1):
        return None
    casos, iguais = int(achou.group(1)), int(achou.group(2))
    divergentes = _DIVERGE.findall(texto)
    if casos <= 0 or iguais > casos or len(divergentes) != casos - iguais:
        return None
    if (codigo == 0) != (iguais == casos):
        return None
    return {"casos": casos, "iguais": iguais, "divergentes": divergentes}


# --- publicação ---------------------------------------------------------------------

def proxima_versao(versao: str) -> str:
    achou = _SEMVER.match(versao)
    if achou is None:
        raise ValueError(f"versão fora de MAJOR.MINOR.PATCH: {versao!r}")
    major, minor, patch = (int(p) for p in achou.groups())
    return f"{major}.{minor}.{patch + 1}"


def _milhar(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def _data_br(iso: str) -> str:
    return _dt.date.fromisoformat(iso).strftime("%d/%m/%Y")


def texto_changelog(meta_antiga: dict, meta_nova: dict, classe: str, versao: str) -> str:
    skill = meta_nova["skill"]
    conf = meta_nova["conferencia"]
    calc = meta_nova["calculadora"]["versao"]
    base_antiga, base_nova = meta_antiga["base"]["versao"], meta_nova["base"]["versao"]
    commit = meta_nova["migracoes"]["commit"][:7]
    if classe == "tabelas":
        linhas = [f"Tabelas novas da Receita Federal na `{skill}`. A conta não mudou."]
    elif classe == "release-sem-codigo":
        linhas = [f"A Receita publicou a versão {calc} da calculadora oficial do Simples Nacional, sem mudança "
                  f"no código de cálculo (`src/main`). A conta da `{skill}` não mudou, só a versão espelhada, "
                  f"que era a {meta_antiga['calculadora']['versao']}."]
    else:
        raise ValueError(f"o robô só publica tabelas ou release sem código, não {classe}")
    linhas.append("")
    if base_antiga != base_nova:
        linhas.append(f"- A base da skill passou da {base_antiga} para a {base_nova}, de "
                      f"{_data_br(meta_nova['base']['data'])}, montada das migrações oficiais no commit `{commit}`.")
    else:
        linhas.append(f"- A base da skill segue na {base_nova}, montada das migrações oficiais no commit `{commit}`.")
    linhas.append(f"- Na conferência de {_data_br(conf['data'])}, {_milhar(conf['casos'])} casos deram o mesmo JSON "
                  f"da calculadora oficial versão {calc} rodando, em {_milhar(conf['casos'])}.")
    linhas.append("- Publicada pelo robô de conferência. O GPT da skill só muda quando for republicado.")
    return f"## {versao}\n\n" + "\n".join(linhas) + "\n"


def _trocar_versao(caminho: Path, antiga: str, nova: str) -> None:
    texto = caminho.read_text(encoding="utf-8")
    trocado, n = re.subn(r'("version"\s*:\s*")' + re.escape(antiga) + '"', r"\g<1>" + nova + '"', texto, count=1)
    if n != 1:
        raise RuntimeError(f"{caminho}: não achei \"version\": \"{antiga}\"")
    caminho.write_text(trocado, encoding="utf-8")


def gravar_meta_em_tudo(raiz: Path, meta: dict) -> None:
    """O mesmo meta no meta.json, na CD2_META da base e no topo do esperadas.json."""
    pasta = Path(raiz) / "conferencia" / meta["skill"]
    config = json.loads((pasta / "config.json").read_text(encoding="utf-8"))
    (pasta / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    receita_base.gravar_meta(Path(raiz) / config["base_destino"], meta)
    esperadas_arquivo = pasta / "esperadas.json"
    if esperadas_arquivo.is_file():
        esperadas = receita_paridade.ler_json(esperadas_arquivo.read_text(encoding="utf-8"))
        esperadas["meta"] = meta
        temporario = esperadas_arquivo.with_suffix(".json.tmp")
        temporario.write_text(receita_paridade.escrever_json(esperadas), encoding="utf-8")
        temporario.replace(esperadas_arquivo)


def aplicar_publicacao(raiz: Path, meta_nova: dict, classe: str, meta_antiga: dict | None = None) -> str:
    """Grava a meta nova, sobe o patch no plugin.core.json e nos três manifestos e escreve a entrada
    do CHANGELOG.md. Devolve a versão nova.

    `meta_antiga` é a meta publicada antes (padrão: o meta.json em disco). O workflow passa a do começo
    da conferência, porque o meta.json em disco já foi trocado pelo da base nova."""
    raiz = Path(raiz)
    if classe not in ("tabelas", "release-sem-codigo"):
        raise ValueError(f"o robô só publica tabelas ou release sem código, não {classe}")
    versoes = {rel: json.loads((raiz / rel).read_text(encoding="utf-8"))["version"] for rel in MANIFESTOS}
    if len(set(versoes.values())) != 1:
        raise RuntimeError(f"versões diferentes nos manifestos: {versoes}")
    atual = versoes[MANIFESTOS[0]]
    nova = proxima_versao(atual)
    changelog_arquivo = raiz / "CHANGELOG.md"
    changelog = changelog_arquivo.read_text(encoding="utf-8")
    if f"\n## {nova}\n" in changelog:
        raise RuntimeError(f"o CHANGELOG.md já tem a versão {nova}")
    if meta_antiga is None:
        meta_antiga = json.loads((raiz / "conferencia" / meta_nova["skill"] / "meta.json").read_text(encoding="utf-8"))
    entrada = texto_changelog(meta_antiga, meta_nova, classe, nova)
    corte = changelog.find("\n## ")
    if corte < 0:
        raise RuntimeError("o CHANGELOG.md não tem nenhuma entrada ## <versão>")
    gravar_meta_em_tudo(raiz, meta_nova)
    for rel in MANIFESTOS:
        _trocar_versao(raiz / rel, atual, nova)
    changelog_arquivo.write_text(changelog[:corte + 1] + entrada + "\n" + changelog[corte + 1:], encoding="utf-8")
    return nova


# --- bases e calculadora oficial ------------------------------------------------------

def _tabelas_de_dados(con: sqlite3.Connection) -> set[str]:
    return {nome for nome in receita_base._tabelas_existentes(con)
            if not nome.startswith("sqlite_") and nome not in ("flyway_schema_history", "CD2_META")}


def comparar_bases(flyway: sqlite3.Connection, montada: sqlite3.Connection,
                   ignorar_colunas: dict | None) -> list[str]:
    """Diferenças entre a base do Flyway oficial e a do receita_base.py, em todas as tabelas de dados.
    Vazia se o conteúdo é o mesmo."""
    a, b = _tabelas_de_dados(flyway), _tabelas_de_dados(montada)
    problemas = [f"tabela só no Flyway: {t}" for t in sorted(a - b)]
    problemas += [f"tabela só no receita_base.py: {t}" for t in sorted(b - a)]
    for tabela in sorted(a & b):
        ignorar = {tabela: ignorar_colunas[tabela]} if ignorar_colunas and tabela in ignorar_colunas else None
        if receita_base.hash_conteudo(flyway, [tabela], ignorar) != receita_base.hash_conteudo(montada, [tabela], ignorar):
            problemas.append(f"conteúdo diferente: {tabela}")
    return problemas


_SO_LOCAL = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def esperar_oficial(url: str, versao: str, base: str, prazo: float = 600, intervalo: float = 5) -> str | None:
    """Espera /versao responder. Devolve None se respondeu com a versão e a base esperadas, senão o motivo."""
    if not re.match(r"^http://(127\.0\.0\.1|localhost)(:\d+)?/", url):
        return f"a calculadora oficial tem de rodar na própria máquina, não em {url}"
    fim = time.monotonic() + prazo
    ultimo = "sem resposta"
    while time.monotonic() < fim:
        try:
            with _SO_LOCAL.open(url.rstrip("/") + "/versao", timeout=10) as resposta:
                corpo = json.loads(resposta.read().decode("utf-8"))
            if corpo.get("versaoApp") != versao:
                return f"a calculadora oficial respondeu a versão {corpo.get('versaoApp')!r}, e a release é a {versao!r}"
            if corpo.get("versaoDb") != base:
                return f"a calculadora oficial respondeu a base {corpo.get('versaoDb')!r}, e a montada é a {base!r}"
            return None
        except (urllib.error.URLError, OSError, ValueError) as erro:
            ultimo = str(erro)
        time.sleep(intervalo)
    return f"a calculadora oficial não respondeu /versao em {prazo:.0f} s ({ultimo})"


# --- textos -------------------------------------------------------------------------

def titulo(tipo: str, skill: str) -> str:
    return {
        "pr-regra": f"Receita: portar a mudança de regra da {skill}",
        "issue-divergencia": f"Receita: a conferência da {skill} não fechou 100%",
        "issue-falha": f"Receita: o robô de conferência da {skill} falhou",
        "issue-gpt": f"Republicar o GPT {skill}",
    }[tipo]


def _estado(upstream: dict) -> str:
    return f"{upstream['calculadora']['versao']}@{upstream['migracoes']['commit'][:12]}"


def marca(tipo: str, upstream: dict, hoje: str) -> str:
    return f"<!-- cd2-robo tipo={tipo} estado={_estado(upstream)} dia={hoje} -->"


def deve_comentar(comentarios: list[str], tipo: str, upstream: dict, hoje: str) -> bool:
    """No máximo um comentário por dia e nenhum repetindo um estado da Receita já comentado.

    Falha comenta uma vez por dia enquanto durar, mesmo com a Receita parada: é o sinal de que o robô
    não está publicando."""
    estado = _estado(upstream)
    for corpo in comentarios:
        for t, e, d in _MARCA.findall(corpo or ""):
            if t == tipo and (d == hoje or (e == estado and tipo != "issue-falha")):
                return False
    return True


def versao_no_ar(corpo: str) -> str | None:
    achou = _NO_AR.search(corpo or "")
    return achou.group(1) if achou else None


def _url_compare(repo: str, de: str, para: str) -> str:
    return f"https://github.com/{repo}/compare/{de}...{para}"


def _resumo_upstream(meta: dict, upstream: dict) -> list[str]:
    calc_repo = meta["calculadora"]["repo"]
    migr_repo = meta["migracoes"]["repo"]
    linhas = []
    de_c, para_c = meta["calculadora"], upstream["calculadora"]
    if para_c["versao"] != de_c["versao"]:
        linhas.append(f"- Calculadora do Simples: da {de_c['versao']} (commit `{de_c['commit'][:7]}`) para a "
                      f"{para_c['versao']} (commit `{para_c['commit'][:7]}`). Diferença: "
                      f"{_url_compare(calc_repo, de_c['commit'], para_c['commit'])}")
    else:
        linhas.append(f"- Calculadora do Simples: segue na {de_c['versao']}.")
    de_m, para_m = meta["migracoes"]["commit"], upstream["migracoes"]["commit"]
    if para_m != de_m:
        linhas.append(f"- Migrações: do commit `{de_m[:7]}` para o `{para_m[:7]}`. Diferença: "
                      f"{_url_compare(migr_repo, de_m, para_m)}")
    else:
        linhas.append(f"- Migrações: seguem no commit `{de_m[:7]}`.")
    base = upstream.get("base")
    if base and base.get("versao"):
        if base["versao"] != meta["base"]["versao"]:
            linhas.append(f"- Base: da {meta['base']['versao']} para a {base['versao']}.")
        else:
            linhas.append(f"- Base: segue na {base['versao']}.")
    return linhas


def _resumo_paridade(paridade: dict | None, versao: str, sem: str = "A conferência não chegou ao fim.") -> list[str]:
    if paridade is None:
        return [sem]
    linhas = [f"{_milhar(paridade['iguais'])} casos iguais em {_milhar(paridade['casos'])}, "
              f"contra a calculadora oficial versão {versao}."]
    divergentes = paridade.get("divergentes") or []
    if divergentes:
        linhas.append("")
        linhas.append("Casos que divergiram" + (f" (os primeiros {MAX_DIVERGENTES})" if len(divergentes) > MAX_DIVERGENTES else "") + ":")
        linhas += [f"- `{d}`" for d in divergentes[:MAX_DIVERGENTES]]
        linhas.append("")
        linhas.append("O relatório inteiro, com as diferenças campo a campo, está no artefato da execução.")
    return linhas


def _cerca(texto: str) -> str:
    maior = max((len(m) for m in re.findall(r"`+", texto)), default=0)
    return "`" * max(3, maior + 1)


def texto_pr_regra(meta: dict, upstream: dict, paridade: dict | None, diff_java: str) -> str:
    skill = meta["skill"]
    calc_repo = meta["calculadora"]["repo"]
    de, para = meta["calculadora"]["commit"], upstream["calculadora"]["commit"]
    topo = [
        f"**As tabelas da `{skill}` estão paradas enquanto este PR estiver aberto.** "
        "O robô não publica base nova em cima de um motor que ele sabe estar atrás. A cada dia de espera, "
        "ele comenta aqui o que mais mudou na Receita.",
        "",
        "A Receita mudou o código de cálculo (`src/main`) da calculadora oficial do Simples. "
        "Este PR traz a base e a meta novas, mas a conta da skill ainda é a da versão espelhada. "
        "O robô não publica: alguém precisa portar a mudança e pedir o merge.",
        "",
        "## O que mudou na Receita",
        "",
        *_resumo_upstream(meta, upstream),
        "",
        "## Conferência",
        "",
        *_resumo_paridade(paridade, upstream["calculadora"]["versao"]),
        "",
    ]
    fim = [
        "",
        "## O que portar",
        "",
        "Siga \"Portar uma mudança de regra\" no `AGENTS.md`.",
        "",
        "- [ ] Ler o diff do Java acima e achar o método correspondente em `simples_motor.py`, `simples_base.py` ou `numeros.py`.",
        "- [ ] Portar método a método: o Python faz o que o Java faz, inclusive nos erros.",
        "- [ ] Portar os testes novos da Receita: os que passam por HTTP para `casos-escritos.json`, os outros para os testes em Python.",
        "- [ ] Rodar a conferência completa até 100% e regravar as esperadas (`receita_paridade.py --gravar-esperadas --amostra 400`).",
        f"- [ ] Atualizar a versão da calculadora no `meta.json` para a {upstream['calculadora']['versao']} e a conferência com a data e os casos.",
        "- [ ] Atualizar a `metodologia.md`, o `SKILL.md` e as instruções do GPT, se citarem algo que mudou.",
        "- [ ] Rodar o sync, o `--check` e os testes, subir a versão (minor) e escrever o `CHANGELOG.md`.",
        "",
        "Depois do merge, a release sai pelo fluxo normal e o robô volta a publicar tabelas sozinho.",
    ]
    cabeca = ["## Diff do Java em `src/main`", "", f"Da {meta['calculadora']['versao']} para a {upstream['calculadora']['versao']}."]
    link = _url_compare(calc_repo, de, para)
    fixo = "\n".join(topo + cabeca + fim)
    folga = LIMITE_CORPO - len(fixo) - 400
    diff = diff_java or ""
    nota = ""
    if not diff.strip():
        nota = "O diff veio vazio. Confira na página de comparação: " + link
        diff = ""
    elif len(diff) > folga:
        corte = diff.rfind("\n", 0, max(folga, 0))
        diff = diff[:corte + 1] if corte >= 0 else ""
        nota = f"O diff foi cortado aqui para caber no limite do GitHub. O inteiro está em {link}"
    corpo = list(cabeca)
    if diff:
        cerca = _cerca(diff)
        corpo += ["", cerca + "diff", diff.rstrip("\n"), cerca]
    if nota:
        corpo += ["", nota]
    return "\n".join(topo + corpo + fim) + "\n"


def texto_issue(tipo: str, skill: str, meta: dict, upstream: dict | None = None, paridade: dict | None = None,
                motivo: str | None = None, execucao: str | None = None, hoje: str | None = None,
                versao_no_ar: str | None = None, versao_nova: str | None = None) -> str:
    hoje = hoje or _dt.date.today().isoformat()
    rodape = [f"Execução: {execucao}"] if execucao else []
    if tipo == "issue-divergencia":
        linhas = [
            f"A Receita mudou as tabelas e a conferência da `{skill}` com a base nova não fechou 100%. "
            "O robô não publicou nada: a skill segue com a base anterior.",
            "",
            "## O que mudou na Receita", "", *_resumo_upstream(meta, upstream),
            "", "## Conferência", "", *_resumo_paridade(paridade, upstream["calculadora"]["versao"]),
            "", "## O que fazer", "",
            "Uma tabela nova que muda a conta, ou uma migração que muda a estrutura de uma tabela, pede port "
            "no motor como uma mudança de regra (veja \"Portar uma mudança de regra\" no `AGENTS.md`). "
            "Rode a conferência local com a base nova, ache o caso que diverge e corrija o motor ou a bateria. "
            "Quando a conferência fechar 100%, o robô publica sozinho no dia seguinte e esta issue pode ser fechada.",
        ]
    elif tipo == "issue-falha":
        linhas = [
            f"O robô de conferência da `{skill}` parou antes de decidir e não publicou nada. "
            "Ele nunca publica no escuro.",
            "",
            f"Etapa: {motivo or 'não informada'}",
            f"Dia: {_data_br(hoje)}",
            "",
            "Abra a execução, leia o log da etapa e corrija o que for nosso. Falha da Receita (build quebrado, "
            "repositório fora do ar) costuma passar sozinha: o robô tenta de novo todo dia e comenta aqui "
            "enquanto falhar. Feche a issue quando uma execução terminar bem.",
        ]
        if upstream:
            linhas += ["", "## Estado da Receita nesta execução", "", *_resumo_upstream(meta, upstream)]
    elif tipo == "issue-gpt":
        linhas = [
            f"Saiu a versão {versao_nova} do plugin, com a `{skill}` na base {meta['base']['versao']} e a "
            f"calculadora {meta['calculadora']['versao']}. O GPT no ar ainda é o da versão {versao_no_ar}.",
            "",
            "O GPT mostra no selo a versão que carrega, então o atraso fica à vista de quem usa. Republique pelo "
            "passo a passo do `gpts/README.md`, com o ZIP `cd2-super-gpts.zip` da release, e feche esta issue.",
            "",
            f"<!-- cd2-robo no-ar={versao_no_ar} -->",
        ]
        return "\n".join(linhas) + "\n"
    elif tipo == "congelado":
        linhas = [
            f"Hoje ({_data_br(hoje)}) a Receita está assim. As tabelas da `{skill}` seguem paradas até este PR ser resolvido.",
            "", *_resumo_upstream(meta, upstream),
            "", *_resumo_paridade(paridade, upstream["calculadora"]["versao"], "A conferência não rodou nesta execução."),
            "", marca(tipo, upstream, hoje),
        ]
    elif tipo == "regra-atualizada":
        linhas = [
            f"A Receita mudou de novo ({_data_br(hoje)}). O corpo do PR foi atualizado com o estado de hoje.",
            "", *_resumo_upstream(meta, upstream),
            "", *_resumo_paridade(paridade, upstream["calculadora"]["versao"]),
            "", "O ramo deste PR não foi mexido: a base de hoje está no artefato da execução.",
            "", marca(tipo, upstream, hoje),
        ]
    else:
        raise ValueError(f"tipo de texto desconhecido: {tipo}")
    if tipo in ("issue-divergencia", "issue-falha"):
        linhas += ["", marca(tipo, upstream or {"calculadora": {"versao": "-"}, "migracoes": {"commit": "-"}}, hoje)]
    return "\n".join(linhas + ([""] + rodape if rodape else [])) + "\n"


# --- linha de comando -----------------------------------------------------------------

def _ler(caminho: str | Path) -> dict:
    return json.loads(Path(caminho).read_text(encoding="utf-8"))


def _saida(**valores) -> None:
    """Escreve em $GITHUB_OUTPUT (quando existe) e no stdout."""
    destino = os.environ.get("GITHUB_OUTPUT")
    for chave, valor in valores.items():
        print(f"{chave}={valor}")
        if destino:
            with open(destino, "a", encoding="utf-8") as arquivo:
                arquivo.write(f"{chave}={valor}\n")


def _pasta(raiz: Path, skill: str) -> Path:
    return raiz / "conferencia" / skill


def _paridade_dos_arquivos(args) -> dict | None:
    if not args.paridade or not Path(args.paridade).is_file():
        return None
    try:
        codigo = int(args.codigo)
    except (TypeError, ValueError):
        return None
    return ler_paridade(Path(args.paridade).read_text(encoding="utf-8", errors="replace"), codigo)


def _cmd_detectar(args) -> int:
    raiz = Path(args.raiz)
    meta = _ler(_pasta(raiz, args.skill) / "meta.json")
    config = _ler(_pasta(raiz, args.skill) / "config.json")
    upstream = _ler(args.upstream)
    caminhos = []
    if args.caminhos:
        caminhos = [linha.strip() for linha in Path(args.caminhos).read_text(encoding="utf-8").splitlines() if linha.strip()]
    classe = classificar(meta, upstream, caminhos, config)
    estado = {"skill": args.skill, "classe": classe, "meta": meta, "upstream": upstream}
    if args.saida:
        Path(args.saida).write_text(json.dumps(estado, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _saida(classe=classe)
    return 0


def _cmd_comparar_bases(args) -> int:
    config = _ler(_pasta(Path(args.raiz), args.skill) / "config.json")
    flyway = sqlite3.connect(f"file:{args.flyway}?mode=ro", uri=True)
    montada = sqlite3.connect(f"file:{args.montada}?mode=ro", uri=True)
    try:
        problemas = comparar_bases(flyway, montada, config.get("hash_ignorar_colunas"))
    finally:
        flyway.close()
        montada.close()
    if problemas:
        print("As bases do Flyway oficial e do receita_base.py diferem:", file=sys.stderr)
        for problema in problemas:
            print(f"  {problema}", file=sys.stderr)
        return 1
    print("A base do Flyway oficial e a do receita_base.py têm o mesmo conteúdo.")
    return 0


def _cmd_esperar_oficial(args) -> int:
    motivo = esperar_oficial(args.url, args.versao, args.base, args.prazo)
    if motivo:
        print(f"erro: {motivo}", file=sys.stderr)
        return 1
    print(f"A calculadora oficial {args.versao} responde em {args.url} com a base {args.base}.")
    return 0


def _cmd_decidir(args) -> int:
    estado = _ler(args.estado) if args.estado and Path(args.estado).is_file() else None
    if estado is None or args.resultado_conferencia not in ("success", ""):
        motivo = "a conferência não terminou"
        if args.etapa and Path(args.etapa).is_file():
            motivo += f": parou em \"{Path(args.etapa).read_text(encoding='utf-8').strip()}\""
        _saida(acao="issue-falha", motivo=motivo)
        return 0
    meta_agora = _ler(_pasta(Path(args.raiz), estado["skill"]) / "meta.json")
    if meta_agora != estado["meta"]:
        print("O meta.json mudou desde o começo da conferência: o robô não age em cima de estado velho e "
              "confere de novo na próxima execução.")
        _saida(acao="terminar", motivo="meta.json mudou durante a execução")
        return 0
    paridade = None if estado["classe"] == "nada" else _paridade_dos_arquivos(args)
    acao = decidir(estado["classe"], paridade, args.pr_aberto == "true")
    motivo = ""
    if acao == "issue-falha":
        motivo = "a conferência completa não chegou ao fim (veja o relatório da paridade no artefato)"
    if args.ramo and args.ramo != "refs/heads/main" and acao not in ("terminar", "issue-falha"):
        print(f"Execução fora da main ({args.ramo}): na main a ação seria {acao}. Aqui o robô só confere.")
        acao, motivo = "terminar", f"fora da main; na main seria {acao}"
    _saida(acao=acao, motivo=motivo or "-")
    return 0


def _cmd_publicar(args) -> int:
    raiz = Path(args.raiz)
    estado = _ler(args.estado)
    paridade = _paridade_dos_arquivos(args)
    if paridade is None or not _cem_por_cento(paridade):
        print("erro: o robô só publica com a conferência completa em 100%", file=sys.stderr)
        return 1
    meta = _ler(_pasta(raiz, estado["skill"]) / "meta.json")
    upstream = estado["upstream"]
    meta["calculadora"]["versao"] = upstream["calculadora"]["versao"]
    meta["calculadora"]["commit"] = upstream["calculadora"]["commit"]
    if meta["migracoes"]["commit"] != upstream["migracoes"]["commit"]:
        print("erro: a base montada não veio do commit das migrações que o robô olhou", file=sys.stderr)
        return 1
    meta["conferencia"] = {"data": args.data or _dt.date.today().isoformat(),
                           "casos": paridade["casos"], "iguais": paridade["iguais"]}
    versao = aplicar_publicacao(raiz, meta, estado["classe"], meta_antiga=estado["meta"])
    _saida(versao=versao, base=meta["base"]["versao"])
    return 0


def _cmd_meta(args) -> int:
    raiz = Path(args.raiz)
    gravar_meta_em_tudo(raiz, _ler(_pasta(raiz, args.skill) / "meta.json"))
    return 0


def _cmd_texto(args) -> int:
    estado = _ler(args.estado) if args.estado and Path(args.estado).is_file() else {}
    skill = args.skill or estado.get("skill")
    raiz = Path(args.raiz)
    meta = estado.get("meta") or _ler(_pasta(raiz, skill) / "meta.json")
    upstream = estado.get("upstream")
    if upstream is not None and args.base_nova and Path(args.base_nova).is_file():
        nova = _ler(args.base_nova)
        upstream = {**upstream, "base": nova.get("base")}
    paridade = _paridade_dos_arquivos(args)
    hoje = args.hoje or _dt.date.today().isoformat()
    if args.tipo == "titulo":
        texto = titulo(args.de, skill) + "\n"
    elif args.tipo == "pr-regra":
        diff = Path(args.diff).read_text(encoding="utf-8", errors="replace") if args.diff and Path(args.diff).is_file() else ""
        texto = texto_pr_regra(meta, upstream, paridade, diff)
    elif args.tipo == "issue-gpt":
        meta_atual = _ler(_pasta(raiz, skill) / "meta.json")
        corpo_atual = Path(args.corpo_atual).read_text(encoding="utf-8") if args.corpo_atual and Path(args.corpo_atual).is_file() else ""
        texto = texto_issue("issue-gpt", skill, meta_atual, versao_nova=args.versao_nova,
                            versao_no_ar=versao_no_ar(corpo_atual) or args.versao_anterior)
    else:
        if args.comentarios and upstream is not None and args.tipo in ("congelado", "regra-atualizada", "issue-divergencia", "issue-falha"):
            comentarios = _ler(args.comentarios) if Path(args.comentarios).is_file() else []
            if not deve_comentar(comentarios, args.tipo, upstream, hoje):
                return 0
        texto = texto_issue(args.tipo, skill, meta, upstream=upstream, paridade=paridade, motivo=args.motivo,
                            execucao=args.execucao, hoje=hoje)
    if args.saida:
        Path(args.saida).write_text(texto, encoding="utf-8")
    else:
        sys.stdout.write(texto)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--raiz", default=str(ROOT), help=argparse.SUPPRESS)
    sub = ap.add_subparsers(dest="comando", required=True)

    p = sub.add_parser("detectar")
    p.add_argument("--skill", required=True)
    p.add_argument("--upstream", required=True, help="JSON com calculadora.versao, calculadora.commit e migracoes.commit")
    p.add_argument("--caminhos", help="arquivo com um caminho mudado por linha")
    p.add_argument("--saida", help="grava o estado (skill, classe, meta, upstream) neste JSON")
    p.set_defaults(func=_cmd_detectar)

    p = sub.add_parser("comparar-bases")
    p.add_argument("--skill", required=True)
    p.add_argument("--flyway", required=True)
    p.add_argument("--montada", required=True)
    p.set_defaults(func=_cmd_comparar_bases)

    p = sub.add_parser("esperar-oficial")
    p.add_argument("--url", required=True)
    p.add_argument("--versao", required=True)
    p.add_argument("--base", required=True)
    p.add_argument("--prazo", type=float, default=600)
    p.set_defaults(func=_cmd_esperar_oficial)

    for nome, func in (("decidir", _cmd_decidir), ("publicar", _cmd_publicar)):
        p = sub.add_parser(nome)
        p.add_argument("--estado", required=nome == "publicar")
        p.add_argument("--paridade", help="saída do receita_paridade.py --completa")
        p.add_argument("--codigo", help="código de saída do receita_paridade.py")
        if nome == "decidir":
            p.add_argument("--pr-aberto", choices=("true", "false"), default="false")
            p.add_argument("--resultado-conferencia", default="", help="resultado do job de conferência")
            p.add_argument("--etapa", help="arquivo com a última etapa que a conferência começou")
            p.add_argument("--ramo", help="GITHUB_REF; fora da main o robô só confere")
        else:
            p.add_argument("--data", help="data da conferência (padrão: hoje)")
        p.set_defaults(func=func)

    p = sub.add_parser("meta")
    p.add_argument("--skill", required=True)
    p.set_defaults(func=_cmd_meta)

    p = sub.add_parser("texto")
    p.add_argument("--tipo", required=True,
                   choices=("titulo", "pr-regra", "issue-divergencia", "issue-falha", "issue-gpt", "congelado", "regra-atualizada"))
    p.add_argument("--de", choices=("pr-regra", "issue-divergencia", "issue-falha", "issue-gpt"), help="com --tipo titulo")
    p.add_argument("--skill")
    p.add_argument("--estado")
    p.add_argument("--paridade")
    p.add_argument("--codigo")
    p.add_argument("--diff")
    p.add_argument("--base-nova", help="meta.json montado da base nova (para dizer a versão da base)")
    p.add_argument("--motivo")
    p.add_argument("--execucao")
    p.add_argument("--hoje")
    p.add_argument("--comentarios", help="JSON com a lista de corpos dos comentários que já existem")
    p.add_argument("--corpo-atual", help="corpo atual da issue do GPT, para manter a versão no ar")
    p.add_argument("--versao-nova")
    p.add_argument("--versao-anterior")
    p.add_argument("--saida")
    p.set_defaults(func=_cmd_texto)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
