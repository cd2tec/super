#!/usr/bin/env python3
"""Robô de conferência com a Receita Federal: decide o que fazer quando a Receita muda.

Roda no workflow `.github/workflows/conferencia-receita.yml`, uma vez por skill de `conferencia/`.
Os subcomandos conversam por arquivos JSON e, no GitHub Actions, por `$GITHUB_OUTPUT`:

    detectar        compara a Receita (upstream.json) com o meta.json da skill e diz a classe da mudança
    comparar-bases  confere que a base do Flyway oficial e a do receita_base.py têm o mesmo conteúdo
    esperar-oficial espera a calculadora oficial responder /versao com a versão e a base esperadas
    reconferir      no job que escreve: valida o artefato e refaz, sem código da Receita, o que dá para refazer
    decidir         junta a classe, a conferência e o PR de regra aberto e diz a ação
    publicar        grava a meta nova, sobe a versão (patch) e escreve o CHANGELOG.md
    meta            copia o meta.json para a CD2_META da base e para o topo do esperadas.json
    achar           acha o PR ou a issue do robô numa lista do `gh` (só os do próprio robô)
    release         diz se a versão do core ainda não tem release e se é preciso disparar a release
    texto           escreve o corpo de PR, issue ou comentário

Tudo o que vem de fora (o artefato do job que roda o código da Receita, as listas do GitHub) é dado:
cada valor passa por um padrão fixo antes de ser usado, e nada que venha de fora vai para o
`$GITHUB_OUTPUT`. Nada aqui fala com a rede além do endereço local da calculadora oficial: quem
conversa com o GitHub é o workflow, pelo `gh`. Nenhum dado de ninguém sai daqui.
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
# A bateria de hoje tem 12.013 casos; um número muito acima disso é saída estragada, não conferência.
MAX_CASOS = 1_000_000
# A API de comparação do GitHub lista no máximo 300 arquivos.
LIMITE_ARQUIVOS_COMPARE = 300
# Quem é o robô. Autor de issue e de PR vem do `gh ... --json author` (GraphQL), com `is_bot`: o login
# do bot do Actions ali é "app/github-actions" ou "github-actions" (não deu para confirmar qual sem uma
# execução de verdade, então valem só esses dois, e sempre com is_bot verdadeiro). Autor de comentário
# vem da API REST, que diz o tipo: login "github-actions[bot]" com type "Bot". O `gh --json comments`
# não traz is_bot no autor do comentário e não serve para isso.
LOGINS_DO_ROBO = frozenset({"github-actions", "app/github-actions"})
LOGIN_REST_DO_ROBO = "github-actions[bot]"
TAG_RELEASE = "cd2-super-v{versao}"

_LINHA_PARIDADE = re.compile(r"^(\d{1,7}) casos, (\d{1,7}) iguais, ", re.MULTILINE)
_DIVERGE = re.compile(r"^DIVERGE (\S+)", re.MULTILINE)
_ID_CASO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
_SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
_VERSAO_RECEITA = re.compile(r"^[0-9]+(\.[0-9]+){0,3}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_BASE = re.compile(r"^V\d{4}$")
_HASH = re.compile(r"^sha256:[0-9a-f]{64}$")
_DATA = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_NO_AR = re.compile(r"<!-- cd2-robo no-ar=(\d+\.\d+\.\d+) -->")
_MARCA = re.compile(r"<!-- cd2-robo tipo=(\S+) estado=(\S+) dia=(\S+) -->")

# Cada saída do $GITHUB_OUTPUT tem um padrão fixo. Nenhum texto livre sai por aqui.
_PADROES_SAIDA = {
    "acao": re.compile("^(" + "|".join(re.escape(a) for a in ACOES) + ")$"),
    "classe": re.compile("^(" + "|".join(re.escape(c) for c in CLASSES) + ")$"),
    "pr": re.compile(r"^[0-9]*$"),
    "numero": re.compile(r"^[0-9]*$"),
    "versao": re.compile(r"^[0-9]+(\.[0-9]+){0,3}$"),
    "base": _BASE,
    "disparar": re.compile(r"^(true|false)$"),
    "situacao": re.compile(r"^(ok|main-andou)$"),
}

# Tamanhos aceitos para o que vem no artefato.
LIMITES_ARTEFATO = {
    "base-receita.db": (100_000, 20_000_000),
    "base-completa.db": (100_000, 60_000_000),
    "esperadas.json": (1_000, 30_000_000),
    "meta.json": (50, 20_000),
    "estado.json": (50, 100_000),
    "paridade.txt": (10, 50_000_000),
    "paridade-codigo.txt": (1, 10),
    "diff-java.txt": (0, 20_000_000),
}


class MudancaInvalida(ValueError):
    """A Receita mudou de um jeito que o robô nunca publica (versão para trás, tag movida)."""


# --- versões --------------------------------------------------------------------------

def partes_da_versao(versao: str):
    """Cópia de calc.py (_partes): "v1.0.2" -> (1, 0, 2); zeros do fim não contam; None fora do padrão."""
    achou = re.fullmatch(r"[vV]?(\d+(?:\.\d+)*)(?:[-+].*)?", (versao or "").strip())
    if achou is None:
        return None
    partes = [int(p) for p in achou.group(1).split(".")]
    while len(partes) > 1 and partes[-1] == 0:
        partes.pop()
    return tuple(partes)


# --- decisão ---------------------------------------------------------------------

def _sob(caminho: str, prefixo: str) -> bool:
    prefixo = prefixo.strip("/")
    return caminho == prefixo or caminho.startswith(prefixo + "/")


def classificar(meta: dict, upstream: dict, caminhos_mudados: list[str], config: dict) -> str:
    """"nada", "tabelas", "release-sem-codigo" ou "regra".

    `caminhos_mudados` traz os caminhos do repositório da calculadora entre o commit espelhado e o
    da release nova, mais os caminhos das migrações (já dentro de `config["migracoes"]["caminho"]`)
    entre o commit espelhado e o novo.

    Levanta MudancaInvalida quando a release nova é anterior à espelhada, ou é a mesma versão com
    outro commit: isso nunca se publica."""
    codigo = config["calculadora"]["caminho_codigo"]
    migracoes = config["migracoes"]["caminho"]
    nova = partes_da_versao(upstream["calculadora"]["versao"])
    espelhada = partes_da_versao(meta["calculadora"]["versao"])
    if nova is None or espelhada is None:
        raise MudancaInvalida(f"versão fora do padrão: release {upstream['calculadora']['versao']!r}, "
                              f"espelhada {meta['calculadora']['versao']!r}")
    if nova < espelhada:
        raise MudancaInvalida(f"a última release ({upstream['calculadora']['versao']}) é anterior à espelhada "
                              f"({meta['calculadora']['versao']})")
    if nova == espelhada:
        if upstream["calculadora"]["commit"] != meta["calculadora"]["commit"]:
            raise MudancaInvalida(f"a release {upstream['calculadora']['versao']} aponta para outro commit "
                                  f"({upstream['calculadora']['commit'][:12]}) que o espelhado "
                                  f"({meta['calculadora']['commit'][:12]})")
        if (upstream["migracoes"]["commit"] != meta["migracoes"]["commit"]
                and any(_sob(c, migracoes) for c in caminhos_mudados)):
            return "tabelas"
        return "nada"
    return "regra" if any(_sob(c, codigo) for c in caminhos_mudados) else "release-sem-codigo"


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
    """Lê a saída do receita_paridade.py. None quando a conferência não chegou ao fim, quando a saída
    e o código de saída se contradizem ou quando um identificador de caso foge do padrão."""
    achou = _LINHA_PARIDADE.search(texto or "")
    if achou is None or codigo not in (0, 1):
        return None
    casos, iguais = int(achou.group(1)), int(achou.group(2))
    divergentes = _DIVERGE.findall(texto)
    if casos <= 0 or casos > MAX_CASOS or iguais > casos or len(divergentes) != casos - iguais:
        return None
    if not all(_ID_CASO.fullmatch(d) for d in divergentes):
        return None
    if (codigo == 0) != (iguais == casos):
        return None
    return {"casos": casos, "iguais": iguais, "divergentes": divergentes}


# --- publicação ---------------------------------------------------------------------

def proxima_versao(versao: str) -> str:
    achou = _SEMVER.fullmatch(versao)
    if achou is None:
        raise ValueError(f"versão fora de MAJOR.MINOR.PATCH: {versao!r}")
    major, minor, patch = (int(p) for p in achou.groups())
    return f"{major}.{minor}.{patch + 1}"


def _milhar(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def _data_br(iso: str) -> str:
    return _dt.date.fromisoformat(iso).strftime("%d/%m/%Y")


def _base_mudou(meta_antiga: dict, meta_nova: dict) -> bool:
    return (meta_antiga["base"]["versao"] != meta_nova["base"]["versao"]
            or meta_antiga["base"]["hash"] != meta_nova["base"]["hash"])


def _config_da(meta: dict, config: dict | None) -> dict:
    """Config da skill: o que o chamador passou ou, sem nada, o `config.json` da pasta da skill."""
    if config is not None:
        return config
    return json.loads((ROOT / "conferencia" / meta["skill"] / "config.json").read_text(encoding="utf-8"))


def _lista_de_arquivos(arquivos: list[str]) -> str:
    itens = [f"`{a}`" for a in arquivos]
    return itens[0] if len(itens) == 1 else ", ".join(itens[:-1]) + " ou " + itens[-1]


def texto_changelog(meta_antiga: dict, meta_nova: dict, classe: str, versao: str, config: dict | None = None) -> str:
    config = _config_da(meta_nova, config)
    nome = texto_seguro(config["nome_humano"])
    codigo = config["calculadora"]["caminho_codigo"]
    skill = meta_nova["skill"]
    conf = meta_nova["conferencia"]
    calc = meta_nova["calculadora"]["versao"]
    base_antiga, base_nova = meta_antiga["base"]["versao"], meta_nova["base"]["versao"]
    commit = meta_nova["migracoes"]["commit"][:7]
    base_mudou = _base_mudou(meta_antiga, meta_nova)
    # "A conta não mudou" só vale quando nem o código nem as tabelas mudaram.
    efeito = ("O código de cálculo não mudou, mas o resultado pode mudar nos itens que as tabelas novas alcançam."
              if base_mudou else "A conta não mudou.")
    if classe == "tabelas":
        linhas = [f"Tabelas novas da Receita Federal na `{skill}`. {efeito}"]
    elif classe == "release-sem-codigo":
        linhas = [f"A Receita publicou a versão {calc} da {nome}, sem mudança "
                  f"no código de cálculo (`{codigo}`). A versão espelhada era a "
                  f"{meta_antiga['calculadora']['versao']}. {efeito}"]
    else:
        raise ValueError(f"o robô só publica tabelas ou release sem código, não {classe}")
    linhas.append("")
    if base_antiga != base_nova:
        linhas.append(f"- A base da skill passou da {base_antiga} para a {base_nova}, de "
                      f"{_data_br(meta_nova['base']['data'])}, montada das migrações oficiais no commit `{commit}`.")
    elif base_mudou:
        linhas.append(f"- A base da skill segue na {base_nova}, com conteúdo novo, montada das migrações oficiais "
                      f"no commit `{commit}`.")
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
    config = json.loads((raiz / "conferencia" / meta_nova["skill"] / "config.json").read_text(encoding="utf-8"))
    entrada = texto_changelog(meta_antiga, meta_nova, classe, nova, config)
    corte = changelog.find("\n## ")
    if corte < 0:
        raise RuntimeError("o CHANGELOG.md não tem nenhuma entrada ## <versão>")
    gravar_meta_em_tudo(raiz, meta_nova)
    for rel in MANIFESTOS:
        _trocar_versao(raiz / rel, atual, nova)
    changelog_arquivo.write_text(changelog[:corte + 1] + entrada + "\n" + changelog[corte + 1:], encoding="utf-8")
    return nova


def deve_disparar_release(versao: str, releases: list, execucoes: list) -> bool:
    """True quando a versão do core não tem a release cd2-super-v<versão> e nenhuma execução do
    release.yml está na fila ou rodando (a de hoje pode só não ter terminado)."""
    if not _SEMVER.fullmatch(versao or ""):
        raise ValueError(f"versão fora de MAJOR.MINOR.PATCH: {versao!r}")
    tags = {r.get("tagName") for r in releases if isinstance(r, dict)}
    if TAG_RELEASE.format(versao=versao) in tags:
        return False
    andando = {"queued", "in_progress", "waiting", "pending", "requested"}
    return not any(isinstance(e, dict) and e.get("status") in andando for e in execucoes)


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


def _abrir_so_leitura(caminho: Path) -> sqlite3.Connection:
    """Abre uma base que veio de fora: só leitura e, onde o Python deixa, no modo defensivo do SQLite."""
    con = sqlite3.connect(f"file:{caminho}?mode=ro", uri=True)
    if hasattr(con, "setconfig") and hasattr(sqlite3, "SQLITE_DBCONFIG_DEFENSIVE"):
        con.setconfig(sqlite3.SQLITE_DBCONFIG_DEFENSIVE, True)
    return con


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
                corpo = json.loads(resposta.read(100_000).decode("utf-8"))
            if corpo.get("versaoApp") != versao:
                return f"a calculadora oficial respondeu a versão {corpo.get('versaoApp')!r}, e a release é a {versao!r}"
            if corpo.get("versaoDb") != base:
                return f"a calculadora oficial respondeu a base {corpo.get('versaoDb')!r}, e a montada é a {base!r}"
            return None
        except (urllib.error.URLError, OSError, ValueError, AttributeError) as erro:
            ultimo = str(erro)
        time.sleep(intervalo)
    return f"a calculadora oficial não respondeu /versao em {prazo:.0f} s ({ultimo})"


# --- o artefato e a reconferência ---------------------------------------------------------

def _validar_upstream(upstream, problemas: list[str]) -> None:
    try:
        if not _VERSAO_RECEITA.fullmatch(upstream["calculadora"]["versao"]):
            problemas.append("versão da calculadora fora do padrão")
        if not _COMMIT.fullmatch(upstream["calculadora"]["commit"]):
            problemas.append("commit da calculadora fora do padrão")
        if not _COMMIT.fullmatch(upstream["migracoes"]["commit"]):
            problemas.append("commit das migrações fora do padrão")
    except (KeyError, TypeError):
        problemas.append("upstream sem calculadora.versao, calculadora.commit ou migracoes.commit")


def _validar_meta_da_base(meta, problemas: list[str]) -> None:
    try:
        if not _BASE.fullmatch(meta["base"]["versao"]):
            problemas.append("versão da base fora do padrão")
        if not _DATA.fullmatch(meta["base"]["data"]):
            problemas.append("data da base fora do padrão")
        if not _HASH.fullmatch(meta["base"]["hash"]):
            problemas.append("hash da base fora do padrão")
        if not _COMMIT.fullmatch(meta["migracoes"]["commit"]):
            problemas.append("commit das migrações do meta fora do padrão")
    except (KeyError, TypeError):
        problemas.append("meta.json sem base ou migracoes")


def _tamanho_ok(arquivo: Path, problemas: list[str]) -> bool:
    minimo, maximo = LIMITES_ARTEFATO[arquivo.name]
    if not arquivo.is_file():
        problemas.append(f"falta {arquivo.name} no artefato")
        return False
    tamanho = arquivo.stat().st_size
    if not minimo <= tamanho <= maximo:
        problemas.append(f"{arquivo.name} com {tamanho} bytes, fora de {minimo} a {maximo}")
        return False
    return True


def validar_artefato(raiz: Path, skill: str, pasta: Path) -> tuple[dict | None, list[str]]:
    """Confere o artefato do job que roda o código da Receita antes de qualquer uso.

    Devolve (estado, problemas). Com problemas, nada do artefato pode ser usado."""
    estado, problemas, _ = examinar_artefato(raiz, skill, pasta)
    return estado, problemas


def examinar_artefato(raiz: Path, skill: str, pasta: Path,
                      meta_do_comeco: dict | None = None) -> tuple[dict | None, list[str], bool]:
    """Como validar_artefato, e diz se a main andou no meio da execução: (estado, problemas, main_andou).

    `meta_do_comeco` é o meta.json no commit que o job conferir usou. Se o artefato traz esse meta e a
    main já tem outro (alguém fez merge no meio), não é falha: main_andou é True e o robô deixa para a
    próxima execução. Meta que não é nem o de agora nem o do começo é falha."""
    raiz, pasta = Path(raiz), Path(pasta)
    problemas: list[str] = []
    config = _ler(_pasta(raiz, skill) / "config.json")
    meta_repo = _ler(_pasta(raiz, skill) / "meta.json")
    if not _tamanho_ok(pasta / "estado.json", problemas):
        return None, problemas, False
    try:
        estado = _ler(pasta / "estado.json")
    except ValueError:
        return None, ["estado.json não é JSON"], False
    if not isinstance(estado, dict) or set(estado) != {"skill", "classe", "meta", "upstream"}:
        return None, ["estado.json fora do formato"], False
    if estado["skill"] != skill:
        problemas.append("estado.json é de outra skill")
    if estado["classe"] not in CLASSES:
        problemas.append("classe fora do conjunto")
    _validar_upstream(estado["upstream"], problemas)
    if estado["meta"] != meta_repo:
        if not problemas and meta_do_comeco is not None and estado["meta"] == meta_do_comeco:
            return estado, [], True
        problemas.append("o meta.json do artefato não é nem o do repositório nem o do commit do começo")
    if problemas or estado["classe"] == "nada":
        return estado, problemas, False

    for nome in ("meta.json", "base-receita.db", "base-completa.db", "esperadas.json", "paridade.txt",
                 "paridade-codigo.txt"):
        _tamanho_ok(pasta / nome, problemas)
    if (pasta / "diff-java.txt").exists():
        _tamanho_ok(pasta / "diff-java.txt", problemas)
    if problemas:
        return estado, problemas, False
    try:
        meta_nova = _ler(pasta / "meta.json")
    except ValueError:
        return estado, ["meta.json do artefato não é JSON"], False
    _validar_meta_da_base(meta_nova, problemas)
    if problemas:
        return estado, problemas, False
    # Fora base e migrações, o receita_base.py não mexe no meta.
    for chave in ("skill", "calculadora", "conferencia"):
        if meta_nova.get(chave) != meta_repo.get(chave):
            problemas.append(f"o meta.json do artefato mudou {chave}")
    if meta_nova["migracoes"] != {"repo": config["migracoes"]["repo"], "commit": estado["upstream"]["migracoes"]["commit"]}:
        problemas.append("o meta.json do artefato não veio do commit das migrações que o robô olhou")
    try:
        if receita_base.ler_meta(pasta / "base-receita.db") != meta_nova:
            problemas.append("a CD2_META da base não é o meta.json do artefato")
        con = _abrir_so_leitura(pasta / "base-receita.db")
        try:
            nomes = _tabelas_de_dados(con)
            if nomes != set(config["tabelas"]):
                problemas.append("a base do artefato não tem exatamente as tabelas do config.json")
            elif receita_base.hash_conteudo(con, config["tabelas"], config.get("hash_ignorar_colunas")) != meta_nova["base"]["hash"]:
                problemas.append("a base do artefato não tem o hash do seu meta.json")
        finally:
            con.close()
        con = _abrir_so_leitura(pasta / "base-completa.db")
        try:
            if receita_base.hash_conteudo(con, config["tabelas"], config.get("hash_ignorar_colunas")) != meta_nova["base"]["hash"]:
                problemas.append("a base completa do artefato não tem o hash do meta.json")
        finally:
            con.close()
    except (sqlite3.Error, RuntimeError, ValueError) as erro:
        problemas.append(f"base do artefato ilegível: {type(erro).__name__}")
    try:
        esperadas = receita_paridade.ler_json((pasta / "esperadas.json").read_text(encoding="utf-8"))
        casos = esperadas["casos"]
        ids = [c["id"] for c in casos]
        if not casos or len(set(ids)) != len(ids) or not all(isinstance(i, str) and _ID_CASO.fullmatch(i) for i in ids):
            problemas.append("esperadas.json com casos fora do formato")
        elif not all(isinstance(c["resposta"]["status"], int) for c in casos):
            problemas.append("esperadas.json com resposta fora do formato")
    except (ValueError, KeyError, TypeError, UnicodeDecodeError):
        problemas.append("esperadas.json fora do formato")
    codigo = (pasta / "paridade-codigo.txt").read_text(encoding="utf-8", errors="replace").strip()
    if not re.fullmatch(r"[0-9]{1,3}", codigo):
        problemas.append("código da paridade fora do padrão")
    return estado, problemas, False


def _arquivos_do_compare(compare) -> tuple[list[str], bool]:
    """(nomes, truncado) da resposta de GET /repos/{repo}/compare/{a}...{b}."""
    if not isinstance(compare, dict) or not isinstance(compare.get("files", []), list):
        raise ValueError("resposta da comparação fora do formato")
    nomes = []
    for arquivo in compare.get("files", []):
        for chave in ("filename", "previous_filename"):
            if isinstance(arquivo, dict) and isinstance(arquivo.get(chave), str):
                nomes.append(arquivo[chave])
    return nomes, len(compare.get("files", [])) >= LIMITE_ARQUIVOS_COMPARE


def reconferir(meta: dict, config: dict, estado: dict, upstream_api: dict,
               compare_calc: dict | None, compare_migr: dict | None) -> list[str]:
    """Refaz, no job que escreve e sem código da Receita, o que dá para refazer: o estado da Receita
    pela API do GitHub e a classe pela lista de arquivos da comparação. Diferença é falha."""
    problemas: list[str] = []
    _validar_upstream(upstream_api, problemas)
    if problemas:
        return ["a API do GitHub devolveu " + p for p in problemas]
    if upstream_api != estado["upstream"]:
        return ["o estado da Receita no artefato não é o que a API do GitHub mostra agora"]
    caminhos: list[str] = []
    truncado = False
    try:
        if upstream_api["calculadora"]["commit"] != meta["calculadora"]["commit"]:
            if compare_calc is None:
                return ["falta a comparação da calculadora"]
            nomes, truncado_calc = _arquivos_do_compare(compare_calc)
            caminhos += nomes
            truncado = truncado or truncado_calc
        if upstream_api["migracoes"]["commit"] != meta["migracoes"]["commit"]:
            if compare_migr is None:
                return ["falta a comparação das migrações"]
            nomes, truncado_migr = _arquivos_do_compare(compare_migr)
            caminhos += [n for n in nomes if _sob(n, config["migracoes"]["caminho"])]
            truncado = truncado or truncado_migr
    except ValueError as erro:
        return [str(erro)]
    try:
        classe = classificar(meta, upstream_api, caminhos, config)
    except MudancaInvalida as erro:
        return [str(erro)]
    if truncado and classe != "regra":
        return ["a comparação do GitHub veio cortada em 300 arquivos e não dá para confirmar a classe"]
    if classe != estado["classe"]:
        return [f"a classe refeita pela API ({classe}) não é a do artefato ({estado['classe']})"]
    return []


# --- quem é o robô no GitHub ---------------------------------------------------------------

def _do_robo(autor) -> bool:
    """O bot do Actions: o login dele e `is_bot` verdadeiro (o `gh --json author` traz os dois)."""
    return isinstance(autor, dict) and autor.get("login") in LOGINS_DO_ROBO and autor.get("is_bot") is True


def pr_do_robo(prs: list, titulo_pr: str, repo: str) -> int | None:
    """O PR de regra do robô: título, ramo `receita/` neste repositório e autor o bot do Actions."""
    dono, _, nome = repo.partition("/")
    for pr in prs if isinstance(prs, list) else []:
        if not isinstance(pr, dict) or pr.get("title") != titulo_pr:
            continue
        cabeca = pr.get("headRepository") or {}
        dono_cabeca = pr.get("headRepositoryOwner") or {}
        if (pr.get("isCrossRepository") is False
                and cabeca.get("name") == nome and dono_cabeca.get("login") == dono
                and str(pr.get("headRefName", "")).startswith("receita/")
                and _do_robo(pr.get("author")) and isinstance(pr.get("number"), int)):
            return pr["number"]
    return None


def issue_do_robo(issues: list, titulo_issue: str) -> int | None:
    for issue in issues if isinstance(issues, list) else []:
        if (isinstance(issue, dict) and issue.get("title") == titulo_issue and _do_robo(issue.get("author"))
                and isinstance(issue.get("number"), int)):
            return issue["number"]
    return None


def corpos_do_robo(comentarios) -> list[str]:
    """Os corpos dos comentários escritos pelo robô, da API REST
    (`gh api repos/<repo>/issues/<n>/comments --paginate --slurp`, que dá uma lista de páginas).

    Só conta comentário com user.type "Bot" e user.login "github-actions[bot]". Comentário no formato
    do `gh --json comments` ({"author": {"login": ...}}) não conta: ali não há como saber se é bot."""
    if not isinstance(comentarios, list):
        return []
    planos = []
    for item in comentarios:
        planos.extend(item if isinstance(item, list) else [item])
    corpos = []
    for c in planos:
        usuario = c.get("user") if isinstance(c, dict) else None
        if isinstance(usuario, dict) and usuario.get("type") == "Bot" and usuario.get("login") == LOGIN_REST_DO_ROBO:
            corpos.append(c.get("body") or "")
    return corpos


# --- textos -------------------------------------------------------------------------

def titulo(tipo: str, skill: str) -> str:
    return {
        "pr-regra": f"Receita: portar a mudança de regra da {skill}",
        "issue-divergencia": f"Receita: a conferência da {skill} não fechou 100%",
        "issue-falha": f"Receita: o robô de conferência da {skill} falhou",
        "issue-gpt": f"Republicar o GPT {skill}",
        "issue-falha-geral": "Receita: o robô de conferência falhou",
    }[tipo]


def texto_seguro(texto: str, limite: int = 500) -> str:
    """Texto de fora como dado num corpo de Markdown: uma linha, curta, sem HTML nem marcação."""
    texto = re.sub(r"[\x00-\x1f\x7f]+", " ", str(texto or ""))
    texto = re.sub(r"\s+", " ", texto).strip()[:limite]
    texto = texto.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    # Sem menção nem link automático: o @ e o esquema da URL ganham um espaço de largura zero,
    # que não aparece na leitura e quebra o reconhecimento do GitHub.
    texto = texto.replace("@", "@\u200b")
    texto = texto.replace("://", ":\u200b//")
    texto = re.sub(r"(?i)\b(www)\.", "\\1\u200b.", texto)
    return re.sub(r"([\\`*_\[\]{}()#+!|~-])", r"\\\1", texto)


def _estado(upstream: dict) -> str:
    return f"{upstream['calculadora']['versao']}@{upstream['migracoes']['commit'][:12]}"


def marca(tipo: str, upstream: dict, hoje: str) -> str:
    return f"<!-- cd2-robo tipo={tipo} estado={_estado(upstream)} dia={hoje} -->"


def deve_comentar(comentarios: list[str], tipo: str, upstream: dict, hoje: str) -> bool:
    """No máximo um comentário por dia e nenhum repetindo um estado da Receita já comentado.

    `comentarios` são corpos já filtrados por `corpos_do_robo`: marca de outra pessoa não conta.
    Falha comenta uma vez por dia enquanto durar, mesmo com a Receita parada: é o sinal de que o robô
    não está publicando."""
    estado = _estado(upstream)
    for corpo in comentarios:
        for t, e, d in _MARCA.findall(corpo or ""):
            if t == tipo and (d == hoje or (e == estado and tipo not in ("issue-falha", "issue-falha-geral"))):
                return False
    return True


def versao_no_ar(corpo: str) -> str | None:
    achou = _NO_AR.search(corpo or "")
    return achou.group(1) if achou else None


def _url_compare(repo: str, de: str, para: str) -> str:
    return f"https://github.com/{repo}/compare/{de}...{para}"


def _resumo_upstream(meta: dict, upstream: dict, config: dict | None = None) -> list[str]:
    config = _config_da(meta, config)
    calc_repo = config["calculadora"]["repo"]
    migr_repo = config["migracoes"]["repo"]
    linhas = []
    de_c, para_c = meta["calculadora"], upstream["calculadora"]
    de_m, para_m = meta["migracoes"]["commit"], upstream["migracoes"]["commit"]
    if calc_repo == migr_repo:
        if para_c["versao"] != de_c["versao"]:
            texto = (f"- Repositório: da {de_c['versao']} (commit `{de_c['commit'][:7]}`) para a "
                     f"{para_c['versao']} (commit `{para_c['commit'][:7]}`).")
        else:
            texto = f"- Repositório: calculadora segue na {de_c['versao']}."
        if para_c["commit"] != de_c["commit"]:
            texto += f" Diferença do código: {_url_compare(calc_repo, de_c['commit'], para_c['commit'])}."
        if para_m != de_m:
            texto += (f" Tabelas: do commit `{de_m[:7]}` para o `{para_m[:7]}`. Diferença das tabelas: "
                      f"{_url_compare(migr_repo, de_m, para_m)}")
        else:
            texto += f" Tabelas: seguem no commit `{de_m[:7]}`."
        linhas.append(texto)
    else:
        nome = texto_seguro(config["nome_humano"])
        if para_c["versao"] != de_c["versao"]:
            linhas.append(f"- Calculadora ({nome}): da {de_c['versao']} (commit `{de_c['commit'][:7]}`) para a "
                          f"{para_c['versao']} (commit `{para_c['commit'][:7]}`). Diferença: "
                          f"{_url_compare(calc_repo, de_c['commit'], para_c['commit'])}")
        else:
            linhas.append(f"- Calculadora ({nome}): segue na {de_c['versao']}.")
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


def texto_pr_regra(meta: dict, upstream: dict, paridade: dict | None, diff_java: str, classe: str = "regra",
                   hoje: str | None = None, config: dict | None = None) -> str:
    config = _config_da(meta, config)
    nome = texto_seguro(config["nome_humano"])
    codigo = config["calculadora"]["caminho_codigo"]
    motores = _lista_de_arquivos(config["arquivos_motor"])
    skill = meta["skill"]
    calc_repo = config["calculadora"]["repo"]
    de, para = meta["calculadora"]["commit"], upstream["calculadora"]["commit"]
    if classe == "release-sem-codigo":
        motivo = (f"A Receita publicou uma release nova da {nome} sem mudança no código de "
                  f"cálculo (`{codigo}`), mas a conferência com a base e a release novas não fechou 100%. "
                  "Este PR traz a base e a meta novas; a conta da skill ainda é a da versão espelhada. "
                  "O robô não publica: alguém precisa achar o que diverge e pedir o merge.")
    else:
        motivo = (f"A Receita mudou o código de cálculo (`{codigo}`) da {nome}. "
                  "Este PR traz a base e a meta novas, mas a conta da skill ainda é a da versão espelhada. "
                  "O robô não publica: alguém precisa portar a mudança e pedir o merge.")
    topo = [
        f"**As tabelas da `{skill}` estão paradas enquanto este PR estiver aberto.** "
        "O robô não publica base nova em cima de um motor que ele sabe estar atrás. A cada dia de espera, "
        "ele comenta aqui o que mais mudou na Receita.",
        "",
        motivo,
        "",
        "## O que mudou na Receita",
        "",
        *_resumo_upstream(meta, upstream, config),
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
        f"- [ ] Ler o diff do Java acima e achar o método correspondente em {motores}.",
        "- [ ] Portar método a método: o Python faz o que o Java faz, inclusive nos erros.",
        "- [ ] Portar os testes novos da Receita: os que passam por HTTP para `casos-escritos.json`, os outros para os testes em Python.",
        "- [ ] Rodar a conferência completa até 100% e regravar as esperadas (`receita_paridade.py --gravar-esperadas --amostra 400`).",
        f"- [ ] Atualizar a versão da calculadora no `meta.json` para a {upstream['calculadora']['versao']} e a conferência com a data e os casos.",
        "- [ ] Atualizar a `metodologia.md`, o `SKILL.md` e as instruções do GPT, se citarem algo que mudou.",
        "- [ ] Rodar o sync, o `--check` e os testes, subir a versão (minor) e escrever o `CHANGELOG.md`.",
        "",
        "Depois do merge, a release sai pelo fluxo normal e o robô volta a publicar tabelas sozinho.",
    ]
    cabeca = [f"## Diff do Java em `{codigo}`", "", f"Da {meta['calculadora']['versao']} para a {upstream['calculadora']['versao']}."]
    link = _url_compare(calc_repo, de, para)
    fixo = "\n".join(topo + cabeca + fim)
    folga = LIMITE_CORPO - len(fixo) - 400
    diff = diff_java or ""
    nota = ""
    if not diff.strip():
        nota = f"O diff de `{codigo}` veio vazio. Confira na página de comparação: " + link
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
    # A marca diz de que estado da Receita o corpo fala: no dia seguinte, sem mudança, não há comentário.
    marca_corpo = marca("regra-atualizada", upstream, hoje or _dt.date.today().isoformat())
    return "\n".join(topo + corpo + fim + ["", marca_corpo]) + "\n"


def texto_issue(tipo: str, skill: str, meta: dict, upstream: dict | None = None, paridade: dict | None = None,
                motivo: str | None = None, execucao: str | None = None, hoje: str | None = None,
                versao_no_ar: str | None = None, versao_nova: str | None = None,
                config: dict | None = None) -> str:
    hoje = hoje or _dt.date.today().isoformat()
    rodape = [f"Execução: {execucao}"] if execucao else []
    if tipo == "issue-divergencia":
        linhas = [
            f"A Receita mudou as tabelas e a conferência da `{skill}` com a base nova não fechou 100%. "
            "O robô não publicou nada: a skill segue com a base anterior.",
            "",
            "## O que mudou na Receita", "", *_resumo_upstream(meta, upstream, config),
            "", "## Conferência", "", *_resumo_paridade(paridade, upstream["calculadora"]["versao"]),
            "", "## O que fazer", "",
            "Uma tabela nova que muda a conta, ou uma migração que muda a estrutura de uma tabela, pede port "
            "no motor como uma mudança de regra (veja \"Portar uma mudança de regra\" no `AGENTS.md`). "
            "Rode a conferência local com a base nova, ache o caso que diverge e corrija o motor ou a bateria. "
            "Quando a conferência fechar 100%, o robô publica sozinho no dia seguinte e esta issue pode ser fechada.",
        ]
    elif tipo in ("issue-falha", "issue-falha-geral"):
        quem = f"O robô de conferência da `{skill}`" if tipo == "issue-falha" else "O robô de conferência"
        linhas = [
            f"{quem} parou antes de decidir e não publicou nada. Ele nunca publica no escuro.",
            "",
            f"Motivo: {texto_seguro(motivo) if motivo else 'não informado'}",
            f"Dia: {_data_br(hoje)}",
            "",
            "Abra a execução, leia o log da etapa e corrija o que for nosso. Falha da Receita (build quebrado, "
            "repositório fora do ar) costuma passar sozinha: o robô tenta de novo todo dia e comenta aqui "
            "enquanto falhar. Feche a issue quando uma execução terminar bem.",
        ]
        if upstream:
            linhas += ["", "## Estado da Receita nesta execução", "", *_resumo_upstream(meta, upstream, config)]
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
            "", *_resumo_upstream(meta, upstream, config),
            "", *_resumo_paridade(paridade, upstream["calculadora"]["versao"], "A conferência não rodou nesta execução."),
            "", marca(tipo, upstream, hoje),
        ]
    elif tipo == "regra-atualizada":
        linhas = [
            f"A Receita mudou de novo ({_data_br(hoje)}). O corpo do PR foi atualizado com o estado de hoje.",
            "", *_resumo_upstream(meta, upstream, config),
            "", *_resumo_paridade(paridade, upstream["calculadora"]["versao"]),
            "", "O ramo deste PR não foi mexido: a base de hoje está no artefato da execução.",
            "", marca(tipo, upstream, hoje),
        ]
    else:
        raise ValueError(f"tipo de texto desconhecido: {tipo}")
    if tipo in ("issue-divergencia", "issue-falha", "issue-falha-geral"):
        linhas += ["", marca(tipo, upstream or {"calculadora": {"versao": "-"}, "migracoes": {"commit": "-"}}, hoje)]
    return "\n".join(linhas + ([""] + rodape if rodape else [])) + "\n"


# --- linha de comando -----------------------------------------------------------------

def _ler(caminho: str | Path):
    return json.loads(Path(caminho).read_text(encoding="utf-8"))


def _saida(**valores) -> None:
    """Escreve em $GITHUB_OUTPUT (quando existe) e no stdout.

    Cada chave tem um padrão fixo, e valor com quebra de linha ou NUL é recusado: nada que venha de
    fora consegue criar outra saída."""
    linhas = []
    for chave, valor in valores.items():
        valor = "" if valor is None else str(valor)
        if any(c in valor for c in ("\r", "\n", "\0")):
            raise ValueError(f"saída {chave} com quebra de linha ou NUL")
        padrao = _PADROES_SAIDA.get(chave)
        if padrao is None:
            raise ValueError(f"saída desconhecida: {chave}")
        if not padrao.fullmatch(valor):
            raise ValueError(f"saída {chave} fora do padrão: {valor[:40]!r}")
        linhas.append(f"{chave}={valor}")
    destino = os.environ.get("GITHUB_OUTPUT")
    for linha in linhas:
        print(linha)
    if destino:
        with open(destino, "a", encoding="utf-8") as arquivo:
            arquivo.write("".join(linha + "\n" for linha in linhas))


def _motivo(args, texto: str) -> None:
    """O motivo vai para um arquivo, nunca para o $GITHUB_OUTPUT. Quem lê é o texto da issue."""
    print(texto)
    if getattr(args, "motivo_saida", None):
        Path(args.motivo_saida).write_text(texto + "\n", encoding="utf-8")


def _pasta(raiz: Path, skill: str) -> Path:
    return Path(raiz) / "conferencia" / skill


def _paridade_dos_arquivos(args) -> dict | None:
    if not args.paridade or not Path(args.paridade).is_file():
        return None
    try:
        codigo = int(str(args.codigo).strip())
    except (TypeError, ValueError):
        return None
    return ler_paridade(Path(args.paridade).read_text(encoding="utf-8", errors="replace"), codigo)


def ler_caminhos(bruto: bytes) -> list[str]:
    """Caminhos do `git diff -z --name-only`: separados por NUL e nunca entre aspas (o -z desliga o
    core.quotePath), então nome com acento ou com quebra de linha chega inteiro."""
    return [parte.decode("utf-8", errors="surrogateescape") for parte in bruto.split(b"\0") if parte]


def _cmd_detectar(args) -> int:
    raiz = Path(args.raiz)
    meta = _ler(_pasta(raiz, args.skill) / "meta.json")
    config = _ler(_pasta(raiz, args.skill) / "config.json")
    upstream = _ler(args.upstream)
    problemas: list[str] = []
    _validar_upstream(upstream, problemas)
    if problemas:
        _motivo(args, "Olhar a Receita: " + "; ".join(problemas))
        return 1
    caminhos = []
    if args.caminhos:
        caminhos = ler_caminhos(Path(args.caminhos).read_bytes())
    try:
        classe = classificar(meta, upstream, caminhos, config)
    except MudancaInvalida as erro:
        _motivo(args, f"Olhar a Receita: {erro}")
        return 1
    estado = {"skill": args.skill, "classe": classe, "meta": meta, "upstream": upstream}
    if args.saida:
        Path(args.saida).write_text(json.dumps(estado, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _saida(classe=classe, versao=upstream["calculadora"]["versao"])
    return 0


def _cmd_comparar_bases(args) -> int:
    config = _ler(_pasta(Path(args.raiz), args.skill) / "config.json")
    flyway = _abrir_so_leitura(Path(args.flyway))
    montada = _abrir_so_leitura(Path(args.montada))
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


def _cmd_reconferir(args) -> int:
    raiz = Path(args.raiz)
    meta_do_comeco = _ler(args.meta_do_comeco) if args.meta_do_comeco and Path(args.meta_do_comeco).is_file() else None
    estado, problemas, main_andou = examinar_artefato(raiz, args.skill, Path(args.artefato), meta_do_comeco)
    if main_andou:
        _motivo(args, "A main mudou o meta.json no meio da execução: o robô não age em cima de estado velho e "
                      "confere de novo na próxima execução.")
        _saida(situacao="main-andou")
        return 0
    if not problemas:
        meta = _ler(_pasta(raiz, args.skill) / "meta.json")
        config = _ler(_pasta(raiz, args.skill) / "config.json")
        versao = args.tag[1:] if args.tag[:1] in ("v", "V") else args.tag
        upstream_api = {"calculadora": {"versao": versao, "commit": args.commit_calc},
                        "migracoes": {"commit": args.commit_migr}}
        compare_calc = _ler(args.compare_calc) if args.compare_calc and Path(args.compare_calc).is_file() else None
        compare_migr = _ler(args.compare_migr) if args.compare_migr and Path(args.compare_migr).is_file() else None
        problemas = reconferir(meta, config, estado, upstream_api, compare_calc, compare_migr)
    if problemas:
        _motivo(args, "Reconferir o artefato: " + "; ".join(problemas))
        return 1
    print("O artefato confere com o repositório e com a API do GitHub.")
    _saida(situacao="ok")
    return 0


def _cmd_decidir(args) -> int:
    estado = _ler(args.estado) if args.estado and Path(args.estado).is_file() else None
    resultado = (args.resultado_conferencia or "").strip()
    if estado is None or resultado != "success":
        motivo = "a conferência não terminou"
        if args.etapa and Path(args.etapa).is_file():
            # dado do artefato: vai só para o arquivo do motivo, e o texto da issue o escapa
            motivo += ": " + Path(args.etapa).read_text(encoding="utf-8", errors="replace")[:500]
        _motivo(args, motivo)
        _saida(acao="issue-falha", pr="")
        return 0
    pr = args.pr if re.fullmatch(r"[0-9]*", args.pr or "") else ""
    if args.skill and estado.get("skill") != args.skill:
        _motivo(args, "o estado do artefato é de outra skill")
        _saida(acao="issue-falha", pr="")
        return 0
    skill = args.skill or estado.get("skill", "")
    if not re.fullmatch(r"[a-z0-9-]+", str(skill)):
        _motivo(args, "nome de skill fora do padrão no estado do artefato")
        _saida(acao="issue-falha", pr="")
        return 0
    meta_agora = _ler(_pasta(Path(args.raiz), skill) / "meta.json")
    if meta_agora != estado["meta"]:
        _motivo(args, "O meta.json mudou desde o começo da conferência: o robô não age em cima de estado velho e "
                      "confere de novo na próxima execução.")
        _saida(acao="terminar", pr=pr)
        return 0
    paridade = None if estado["classe"] == "nada" else _paridade_dos_arquivos(args)
    acao = decidir(estado["classe"], paridade, bool(pr))
    if acao == "issue-falha":
        _motivo(args, "a conferência completa não chegou ao fim (veja o relatório da paridade no artefato)")
    if args.ramo != "refs/heads/main":
        _motivo(args, f"Execução fora da main: na main a ação seria {acao}. Aqui o robô só confere e não escreve nada.")
        acao = "terminar"
    _saida(acao=acao, pr=pr)
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


def _cmd_achar(args) -> int:
    lista = _ler(args.lista) if Path(args.lista).is_file() else []
    titulo_procurado = titulo(args.de, args.skill)
    if args.tipo == "pr":
        numero = pr_do_robo(lista, titulo_procurado, args.repo)
    else:
        numero = issue_do_robo(lista, titulo_procurado)
    _saida(numero="" if numero is None else str(numero))
    return 0


def _cmd_release(args) -> int:
    releases = _ler(args.releases) if Path(args.releases).is_file() else []
    execucoes = _ler(args.execucoes) if Path(args.execucoes).is_file() else []
    _saida(disparar="true" if deve_disparar_release(args.versao, releases, execucoes) else "false")
    return 0


def _cmd_texto(args) -> int:
    estado = _ler(args.estado) if args.estado and Path(args.estado).is_file() else {}
    skill = args.skill or ""
    if skill and not re.fullmatch(r"[a-z0-9-]+", skill):
        raise ValueError(f"nome de skill fora do padrão: {skill!r}")
    raiz = Path(args.raiz)
    meta = _ler(_pasta(raiz, skill) / "meta.json") if skill else {}
    config = _ler(_pasta(raiz, skill) / "config.json") if skill else None
    upstream = estado.get("upstream") if isinstance(estado, dict) else None
    # O estado pode vir de um artefato que não passou na validação (é por isso que a issue de falha
    # existe): valor fora do padrão não entra em texto nenhum.
    if upstream is not None:
        problemas: list[str] = []
        _validar_upstream(upstream, problemas)
        if problemas or estado.get("meta") != meta or estado.get("skill") != skill:
            upstream = None
    if upstream is not None and args.base_nova and Path(args.base_nova).is_file():
        nova = _ler(args.base_nova)
        problemas = []
        _validar_meta_da_base(nova, problemas)
        if not problemas:
            upstream = {**upstream, "base": nova.get("base")}
    paridade = _paridade_dos_arquivos(args)
    hoje = args.hoje or _dt.date.today().isoformat()
    motivo = None
    if args.motivo_arquivo and Path(args.motivo_arquivo).is_file():
        motivo = Path(args.motivo_arquivo).read_text(encoding="utf-8", errors="replace")
    if args.tipo == "pr-regra":
        diff = Path(args.diff).read_text(encoding="utf-8", errors="replace") if args.diff and Path(args.diff).is_file() else ""
        texto = texto_pr_regra(meta, upstream, paridade, diff, estado.get("classe", "regra"), hoje, config)
    elif args.tipo == "issue-gpt":
        meta_atual = _ler(_pasta(raiz, skill) / "meta.json")
        no_ar = None
        if args.corpo_atual and Path(args.corpo_atual).is_file():
            atual = _ler(args.corpo_atual)
            if isinstance(atual, dict) and _do_robo(atual.get("author")):
                no_ar = versao_no_ar(atual.get("body"))
        texto = texto_issue("issue-gpt", skill, meta_atual, versao_nova=args.versao_nova,
                            versao_no_ar=no_ar or args.versao_anterior)
    else:
        if args.comentarios:
            comentarios = corpos_do_robo(_ler(args.comentarios)) if Path(args.comentarios).is_file() else []
            # o corpo da issue ou do PR também conta, se for do robô (o autor vem do gh --json author)
            if args.corpo_atual and Path(args.corpo_atual).is_file():
                atual = _ler(args.corpo_atual)
                if isinstance(atual, dict) and _do_robo(atual.get("author")):
                    comentarios.append(atual.get("body") or "")
            if not deve_comentar(comentarios, args.tipo,
                                 upstream or {"calculadora": {"versao": "-"}, "migracoes": {"commit": "-"}}, hoje):
                return 0
        texto = texto_issue(args.tipo, skill, meta, upstream=upstream, paridade=paridade, motivo=motivo,
                            execucao=args.execucao, hoje=hoje, config=config)
    if args.saida:
        Path(args.saida).write_text(texto, encoding="utf-8")
    else:
        sys.stdout.write(texto)
    return 0


_PADROES_CONFIG = {
    "oficial.contexto": re.compile(r"/[a-z0-9/-]*"),
    "oficial.jar": re.compile(r"target/[A-Za-z0-9._-]+\.jar"),
    "oficial.flyway_conf": re.compile(r"\./flyway/[A-Za-z0-9._-]+\.conf"),
    "oficial.arquivo_db": re.compile(r"[A-Za-z0-9._-]+\.db"),
}


def valor_do_config(config: dict, campo: str) -> str:
    """Um valor do config.json pelo caminho pontuado, validado por padrão fixo quando o campo é
    um dos que o workflow usa em comando. Levanta ValueError se faltar ou estiver fora do padrão."""
    valor = config
    for parte in campo.split("."):
        if not isinstance(valor, dict) or parte not in valor:
            raise ValueError(f"o config não tem o campo {campo}")
        valor = valor[parte]
    if isinstance(valor, (dict, list)) or valor is None or isinstance(valor, bool):
        raise ValueError(f"o campo {campo} não é um texto nem um número")
    if campo == "oficial.porta":
        if not isinstance(valor, int) or not 1024 <= valor <= 65535:
            raise ValueError(f"oficial.porta fora de 1024 a 65535: {valor!r}")
        return str(valor)
    texto = str(valor)
    if any(c in texto for c in ("\r", "\n", "\0")):
        raise ValueError(f"o campo {campo} tem quebra de linha ou NUL")
    padrao = _PADROES_CONFIG.get(campo)
    if padrao is not None and not padrao.fullmatch(texto):
        raise ValueError(f"{campo} fora do padrão: {texto[:60]!r}")
    return texto


def _cmd_config(args) -> int:
    if not re.fullmatch(r"[a-z0-9-]+", args.skill):
        print(f"erro: nome de skill fora do padrão: {args.skill!r}", file=sys.stderr)
        return 2
    try:
        config = _ler(_pasta(Path(args.raiz), args.skill) / "config.json")
        print(valor_do_config(config, args.campo))
    except (ValueError, OSError) as erro:
        print(f"erro: {erro}", file=sys.stderr)
        return 2
    return 0


def _cmd_titulo(args) -> int:
    print(titulo(args.de, args.skill))
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
    p.add_argument("--motivo-saida", help="arquivo do motivo, quando para")
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

    p = sub.add_parser("reconferir")
    p.add_argument("--skill", required=True)
    p.add_argument("--artefato", required=True, help="pasta do artefato do job conferir")
    p.add_argument("--tag", required=True, help="tag da última release, pela API do GitHub")
    p.add_argument("--commit-calc", required=True, help="commit da tag, pela API do GitHub")
    p.add_argument("--commit-migr", required=True, help="HEAD das migrações, pelo git ls-remote")
    p.add_argument("--compare-calc", help="resposta da API de comparação da calculadora")
    p.add_argument("--compare-migr", help="resposta da API de comparação das migrações")
    p.add_argument("--meta-do-comeco", help="meta.json no commit que o job conferir usou (git show $GITHUB_SHA:...)")
    p.add_argument("--motivo-saida")
    p.set_defaults(func=_cmd_reconferir)

    for nome, func in (("decidir", _cmd_decidir), ("publicar", _cmd_publicar)):
        p = sub.add_parser(nome)
        p.add_argument("--estado", required=nome == "publicar")
        p.add_argument("--paridade", help="saída do receita_paridade.py --completa")
        p.add_argument("--codigo", help="código de saída do receita_paridade.py")
        if nome == "decidir":
            p.add_argument("--pr", default="", help="número do PR de regra do robô aberto (vazio se não há)")
            p.add_argument("--skill", default="", help="a skill da matriz; o estado tem de ser dela")
            p.add_argument("--resultado-conferencia", default="", help="resultado do job de conferência")
            p.add_argument("--etapa", help="arquivo com a última etapa que a conferência começou")
            p.add_argument("--ramo", default="", help="GITHUB_REF; fora da main o robô só confere")
            p.add_argument("--motivo-saida")
        else:
            p.add_argument("--data", help="data da conferência (padrão: hoje)")
        p.set_defaults(func=func)

    p = sub.add_parser("meta")
    p.add_argument("--skill", required=True)
    p.set_defaults(func=_cmd_meta)

    p = sub.add_parser("titulo")
    p.add_argument("--de", required=True, choices=("pr-regra", "issue-divergencia", "issue-falha", "issue-gpt", "issue-falha-geral"))
    p.add_argument("--skill", default="")
    p.set_defaults(func=_cmd_titulo)

    p = sub.add_parser("achar")
    p.add_argument("--tipo", required=True, choices=("pr", "issue"))
    p.add_argument("--de", required=True, choices=("pr-regra", "issue-divergencia", "issue-falha", "issue-gpt", "issue-falha-geral"))
    p.add_argument("--skill", default="")
    p.add_argument("--lista", required=True, help="JSON do gh pr list ou gh issue list")
    p.add_argument("--repo", default="", help="dono/nome deste repositório")
    p.set_defaults(func=_cmd_achar)

    p = sub.add_parser("release")
    p.add_argument("--versao", required=True)
    p.add_argument("--releases", required=True, help="JSON do gh release list --json tagName")
    p.add_argument("--execucoes", required=True, help="JSON do gh run list --workflow release.yml --json status")
    p.set_defaults(func=_cmd_release)

    p = sub.add_parser("config")
    p.add_argument("--skill", required=True)
    p.add_argument("--campo", required=True, help="caminho pontuado, por exemplo oficial.porta")
    p.set_defaults(func=_cmd_config)

    p = sub.add_parser("texto")
    p.add_argument("--tipo", required=True,
                   choices=("pr-regra", "issue-divergencia", "issue-falha", "issue-falha-geral", "issue-gpt",
                            "congelado", "regra-atualizada"))
    p.add_argument("--skill")
    p.add_argument("--estado")
    p.add_argument("--paridade")
    p.add_argument("--codigo")
    p.add_argument("--diff")
    p.add_argument("--base-nova", help="meta.json montado da base nova (para dizer a versão da base)")
    p.add_argument("--motivo-arquivo", help="arquivo com o motivo da falha (dado, escapado no texto)")
    p.add_argument("--execucao")
    p.add_argument("--hoje")
    p.add_argument("--comentarios", help="JSON do gh com os comentários que já existem")
    p.add_argument("--corpo-atual", help="JSON do gh issue view --json body,author da issue do GPT")
    p.add_argument("--versao-nova")
    p.add_argument("--versao-anterior")
    p.add_argument("--saida")
    p.set_defaults(func=_cmd_texto)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
