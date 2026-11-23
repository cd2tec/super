#!/usr/bin/env python3
"""Monta a base reduzida da Receita Federal que uma skill leva dentro do pacote.

A base sai das migrações SQL oficiais (as mesmas do Flyway, na mesma ordem), montadas
em memória. Nenhum código do repositório da Receita roda aqui: só o SQL das tabelas.

Uso:
    python3 scripts/receita_base.py --skill <skill> --repo <clone das migrações> [--completa <saida.db>]

Tudo o que é específico da skill vem de conferencia/<skill>/config.json: lista de
tabelas, caminho das migrações no clone e destino da base. O meta.json da mesma pasta
recebe `base` e `migracoes`; o JSON inteiro também vai para a tabela CD2_META da base.

O hash ignora as colunas de `hash_ignorar_colunas` do config (hoje VERSAO_BASE_DADO.VRBD_DATA),
porque as migrações V0008 a V0012 gravam `datetime('now')` e a data muda a cada montagem.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# O Flyway roda cada migração numa transação; as que abrem a sua ou mexem em
# PRAGMA foreign_keys rodam fora dela.
# `BEGIN` só conta como instrução (o de corpo de TRIGGER não é seguido de `;`).
_DONA_DA_TRANSACAO = re.compile(
    r"^[ \t]*(BEGIN(\s+(DEFERRED|IMMEDIATE|EXCLUSIVE))?(\s+TRANSACTION)?[ \t]*;|PRAGMA\s+foreign_keys\b)",
    re.IGNORECASE | re.MULTILINE)
_COMENTARIOS = re.compile(r"/\*.*?\*/|--[^\n]*", re.DOTALL)
_SHA = re.compile(r"^[0-9a-f]{40}$")
_VERSAO = re.compile(r"^V(\d+)__.*\.sql$")


def _dona_da_transacao(sql: str) -> bool:
    return bool(_DONA_DA_TRANSACAO.search(_COMENTARIOS.sub("", sql)))


def _executar(con: sqlite3.Connection, arquivo: Path, em_transacao: bool) -> None:
    try:
        sql = arquivo.read_text(encoding="utf-8")
        if em_transacao and not _dona_da_transacao(sql):
            sql = f"BEGIN;\n{sql}\nCOMMIT;"
        con.executescript(sql)
    except (sqlite3.Error, OSError, UnicodeDecodeError) as erro:
        if con.in_transaction:
            con.execute("ROLLBACK")
        raise RuntimeError(f"Falhou em {arquivo.name}: {erro}") from erro


def _autorizador(acao, *_):
    if acao in (sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH):
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def montar(sql_dir: Path) -> sqlite3.Connection:
    """Banco em memória com todas as tabelas, na ordem do Flyway."""
    sql_dir = Path(sql_dir)
    criacao = sql_dir / "criacao"
    manutencao = sql_dir / "manutencao"
    baseline = sorted(criacao.glob("B0001__*.sql"))
    if len(baseline) != 1:
        raise RuntimeError(f"Esperava um B0001__*.sql em {criacao}, achei {len(baseline)}")
    antes = criacao / "beforeMigrate.sql"
    depois = criacao / "afterMigrate.sql"
    for obrigatorio in (antes, depois):
        if not obrigatorio.is_file():
            raise RuntimeError(f"Falhou em {obrigatorio.name}: arquivo não encontrado em {criacao}")
    versoes = []
    for arquivo in manutencao.glob("V*.sql"):
        achou = _VERSAO.match(arquivo.name)
        if achou:
            versoes.append((int(achou.group(1)), arquivo))
    versoes.sort(key=lambda par: (par[0], par[1].name))

    con = sqlite3.connect(":memory:", isolation_level=None)
    # O SQL vem de outro repositório: nada de abrir ou criar arquivo por ATTACH.
    con.set_authorizer(_autorizador)
    _executar(con, antes, False)
    _executar(con, baseline[0], False)
    for _, arquivo in versoes:
        _executar(con, arquivo, True)
    _executar(con, depois, False)
    return con


def _tabelas_existentes(con: sqlite3.Connection) -> set[str]:
    return {linha[0] for linha in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def reduzir(origem: sqlite3.Connection, tabelas: list[str], destino: Path) -> None:
    """Grava em `destino` só as tabelas pedidas, com esquema e linhas.

    Os índices de desempenho da Receita ficam de fora (a base passaria de 3 MB e a
    skill carrega tudo em memória); ficam os índices das chaves primárias e únicas."""
    existentes = _tabelas_existentes(origem)
    faltam = [t for t in tabelas if t not in existentes]
    if faltam:
        raise RuntimeError(f"Tabelas que não existem na base: {', '.join(faltam)}")
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    if destino.exists():
        destino.unlink()
    out = sqlite3.connect(destino, isolation_level=None)
    try:
        # As chaves estrangeiras apontam para tabelas que podem não vir junto: não checar.
        out.execute("PRAGMA foreign_keys = OFF")
        for nome in tabelas:
            ddl = origem.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (nome,)).fetchone()[0]
            out.execute(ddl)
        out.execute("BEGIN")
        for nome in tabelas:
            colunas = [c[1] for c in origem.execute(f'PRAGMA table_info("{nome}")')]
            marcas = ", ".join("?" for _ in colunas)
            lista = ", ".join(f'"{c}"' for c in colunas)
            linhas = origem.execute(f'SELECT {lista} FROM "{nome}"')
            out.executemany(f'INSERT INTO "{nome}" ({lista}) VALUES ({marcas})', linhas)
        out.execute("COMMIT")
        out.execute("PRAGMA journal_mode = DELETE")
        out.execute("VACUUM")
    finally:
        out.close()


def hash_conteudo(con: sqlite3.Connection, tabelas: list[str],
                  ignorar_colunas: dict[str, list[str]] | None = None) -> str:
    """SHA-256 de um dump canônico: tabelas em ordem alfabética, colunas na ordem do
    esquema, linhas ordenadas pelo valor de todas as colunas, valores em repr.

    `ignorar_colunas` ({tabela: [colunas]}) tira essas colunas do dump. Coluna que não
    existe na tabela é erro."""
    ignorar_colunas = ignorar_colunas or {}
    h = hashlib.sha256()
    for nome in sorted(set(tabelas)):
        todas = [c[1] for c in con.execute(f'PRAGMA table_info("{nome}")')]
        if not todas:
            raise RuntimeError(f"Tabela que não existe na base: {nome}")
        fora = ignorar_colunas.get(nome, [])
        inexistentes = [c for c in fora if c not in todas]
        if inexistentes:
            raise RuntimeError(f"Coluna ignorada que não existe em {nome}: {', '.join(inexistentes)}")
        colunas = [c for c in todas if c not in fora]
        h.update(f"#{nome}|{','.join(colunas)}\n".encode("utf-8"))
        lista = ", ".join(f'"{c}"' for c in colunas)
        linhas = sorted(
            tuple(repr(v) for v in linha) for linha in con.execute(f'SELECT {lista} FROM "{nome}"'))
        for linha in linhas:
            h.update(("|".join(linha) + "\n").encode("utf-8"))
    return f"sha256:{h.hexdigest()}"


def gravar_meta(destino: Path, meta: dict) -> None:
    """Grava o JSON inteiro na tabela CD2_META (chave `meta`) da base."""
    con = sqlite3.connect(destino)
    try:
        con.execute("CREATE TABLE IF NOT EXISTS CD2_META (chave TEXT PRIMARY KEY, valor TEXT)")
        con.execute(
            "INSERT OR REPLACE INTO CD2_META (chave, valor) VALUES ('meta', ?)",
            (json.dumps(meta, ensure_ascii=False, indent=2),))
        con.commit()
    finally:
        con.close()


def ler_meta(base: Path) -> dict:
    con = sqlite3.connect(f"file:{base}?mode=ro", uri=True)
    try:
        try:
            linha = con.execute("SELECT valor FROM CD2_META WHERE chave = 'meta'").fetchone()
        except sqlite3.Error as erro:
            raise RuntimeError(f"A base {Path(base).name} não tem CD2_META: {erro}") from erro
    finally:
        con.close()
    if linha is None:
        raise RuntimeError(f"A base {Path(base).name} não tem a chave meta em CD2_META")
    return json.loads(linha[0])


def _commit_do_clone(repo: Path) -> str:
    """Lê o commit do HEAD direto dos arquivos do .git, sem rodar o git no clone."""
    git = Path(repo) / ".git"
    head = (git / "HEAD").read_text(encoding="utf-8").strip()
    if head.startswith("ref:"):
        ref = head.split(":", 1)[1].strip()
        if not ref.startswith("refs/") or ".." in ref.split("/"):
            raise RuntimeError(f"Ref inválida em {git / 'HEAD'}: {ref}")
        commit = None
        arquivo = git / ref
        if arquivo.is_file():
            commit = arquivo.read_text(encoding="utf-8").strip()
        elif (git / "packed-refs").is_file():
            for linha in (git / "packed-refs").read_text(encoding="utf-8").splitlines():
                if linha.endswith(f" {ref}"):
                    commit = linha.split()[0]
        if commit is None:
            raise RuntimeError(f"Não achei o commit de {ref} em {git}")
    else:
        commit = head
    if not _SHA.match(commit):
        raise RuntimeError(f"Commit inválido em {git}: {commit!r}")
    return commit


# O esquema de VERSAO_BASE_DADO é o mesmo nos dois repositórios da Receita (regime geral e Simples).
def _versao_da_base(con: sqlite3.Connection) -> tuple[str, str]:
    linha = con.execute(
        "SELECT VRBD_VERSAO_BASE_DADO, VRBD_DATA FROM VERSAO_BASE_DADO ORDER BY VRBD_ID DESC LIMIT 1"
    ).fetchone()
    if linha is None:
        raise RuntimeError("VERSAO_BASE_DADO está vazia")
    return str(linha[0]), str(linha[1])[:10]


def _meta_inicial(config: dict) -> dict:
    return {
        "skill": config["skill"],
        "calculadora": {"repo": config["calculadora"]["repo"], "versao": "", "commit": ""},
        "migracoes": {"repo": config["migracoes"]["repo"], "commit": ""},
        "base": {"versao": "", "data": "", "hash": ""},
        "conferencia": {"data": "", "casos": 0, "iguais": 0},
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--skill", required=True, help="nome da skill (pasta em conferencia/)")
    ap.add_argument("--repo", required=True, type=Path, help="clone das migrações da Receita")
    ap.add_argument("--completa", type=Path, help="grava também a base completa neste arquivo")
    args = ap.parse_args(argv)

    pasta = ROOT / "conferencia" / args.skill
    config = json.loads((pasta / "config.json").read_text(encoding="utf-8"))
    meta_arquivo = pasta / "meta.json"
    meta = (json.loads(meta_arquivo.read_text(encoding="utf-8"))
            if meta_arquivo.is_file() else _meta_inicial(config))

    repo = args.repo.resolve()
    con = montar(repo / config["migracoes"]["caminho"])
    if args.completa:
        args.completa.parent.mkdir(parents=True, exist_ok=True)
        if args.completa.exists():
            args.completa.unlink()
        completa = sqlite3.connect(args.completa)
        con.backup(completa)
        completa.close()

    destino = ROOT / config["base_destino"]
    reduzir(con, config["tabelas"], destino)
    versao, data = _versao_da_base(con)
    meta["migracoes"] = {"repo": config["migracoes"]["repo"], "commit": _commit_do_clone(repo)}
    meta["base"] = {"versao": versao, "data": data, "hash": hash_conteudo(con, config["tabelas"], config.get("hash_ignorar_colunas"))}
    meta_arquivo.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    gravar_meta(destino, meta)
    print(f"{destino.relative_to(ROOT)}: {destino.stat().st_size} bytes, base {versao} ({data}), {meta['base']['hash']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
