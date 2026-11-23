#!/usr/bin/env python3
"""Compara a conta de uma skill com a calculadora oficial da Receita Federal.

Uso:
    python3 scripts/receita_paridade.py --skill <skill> --oficial <url> --gravar-esperadas
    python3 scripts/receita_paridade.py --skill <skill> --oficial <url> [--amostra N | --completa]

Tudo o que é específico da skill vem de conferencia/<skill>/:
  config.json          lista `ignorar` (campos que mudam sozinhos, em caminhos com curinga)
  meta.json            versões espelhadas; vai para o `meta` do esperadas.json
  casos-escritos.json  as requisições (sem `resposta`)
  esperadas.json       as requisições com as respostas oficiais gravadas
  bateria.py           exporta responder(requisicao) -> {"status", "corpo"}: a conta local. Pode exportar
                       também casos(base) -> lista de casos e amostra(casos, n) -> n casos: então --completa
                       compara a lista gerada e --gravar-esperadas --amostra N grava a amostra dela

A URL base já termina no contexto da API (por exemplo http://127.0.0.1:8082/api); o
`caminho` de cada caso é relativo a ela e inclui a query string.

Números do JSON ficam como int ou decimal.Decimal, com a forma escrita (1.0 e 1.00 são
diferentes). Texto compara como texto. A ordem das chaves não conta; a ordem das listas conta.
"""

from __future__ import annotations

import argparse
import copy
import decimal
import importlib.util
import json
import random
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
AMOSTRA_PADRAO = 20


class OficialIndisponivel(RuntimeError):
    """A calculadora oficial não respondeu."""


# --- JSON com números exatos -------------------------------------------------

def ler_json(texto: str) -> Any:
    return json.loads(texto, parse_float=decimal.Decimal)


def escrever_json(valor: Any, indent: int | None = 2) -> str:
    """Escreve JSON mantendo Decimal na forma escrita. ler_json(escrever_json(x)) devolve x."""
    partes: list[str] = []
    _escrever(valor, partes, indent, 0)
    return "".join(partes) + ("\n" if indent is not None else "")


def _escrever(valor: Any, saida: list[str], indent: int | None, nivel: int) -> None:
    if isinstance(valor, dict):
        itens = [(json.dumps(str(k), ensure_ascii=False), v) for k, v in valor.items()]
        _escrever_colecao(saida, indent, nivel, "{", "}", itens, com_chave=True)
    elif isinstance(valor, (list, tuple)):
        _escrever_colecao(saida, indent, nivel, "[", "]", [(None, v) for v in valor], com_chave=False)
    elif isinstance(valor, bool):
        saida.append("true" if valor else "false")
    elif valor is None:
        saida.append("null")
    elif isinstance(valor, decimal.Decimal):
        if not valor.is_finite():
            raise ValueError(f"número não representável em JSON: {valor}")
        saida.append(str(valor))
    elif isinstance(valor, int):
        saida.append(str(valor))
    elif isinstance(valor, str):
        saida.append(json.dumps(valor, ensure_ascii=False))
    else:
        raise TypeError(f"tipo sem representação exata em JSON: {type(valor).__name__}")


def _escrever_colecao(saida, indent, nivel, abre, fecha, itens, com_chave):
    if not itens:
        saida.append(abre + fecha)
        return
    saida.append(abre)
    for i, (chave, valor) in enumerate(itens):
        if indent is not None:
            saida.append("\n" + " " * (indent * (nivel + 1)))
        if com_chave:
            saida.append(chave + (": " if indent is not None else ":"))
        _escrever(valor, saida, indent, nivel + 1)
        if i < len(itens) - 1:
            saida.append("," if indent is not None else ",")
    if indent is not None:
        saida.append("\n" + " " * (indent * nivel))
    saida.append(fecha)


# --- chamada ao serviço oficial ------------------------------------------------

_ABRIDOR = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def chamar_oficial(url_base: str, requisicao: dict, prazo: float = 60) -> dict:
    """Faz a requisição e devolve {"status", "tipo", "corpo"}.

    corpo: None se a resposta veio vazia; o texto cru se não for JSON; senão o JSON lido
    (números como int ou Decimal). tipo: o Content-Type da resposta (None se não veio).
    """
    url = url_base.rstrip("/") + requisicao["caminho"]
    metodo = requisicao.get("metodo", "GET")
    corpo = requisicao.get("corpo")
    dados = None
    cabecalhos = {"Accept": "application/json"}
    if corpo is not None:
        dados = escrever_json(corpo, indent=None).encode("utf-8")
        cabecalhos["Content-Type"] = "application/json"
    pedido = urllib.request.Request(url, data=dados, method=metodo, headers=cabecalhos)
    try:
        with _ABRIDOR.open(pedido, timeout=prazo) as resposta:
            status, bruto, tipo = resposta.status, resposta.read(), resposta.headers.get("Content-Type")
    except urllib.error.HTTPError as erro:
        status, bruto, tipo = erro.code, erro.read(), erro.headers.get("Content-Type")
    except (urllib.error.URLError, OSError) as erro:
        raise OficialIndisponivel(f"A calculadora oficial não respondeu em {url}: {erro}") from erro
    return {"status": status, "tipo": tipo, "corpo": _ler_corpo(bruto, tipo)}


def _ler_corpo(bruto: bytes, tipo: str | None) -> Any:
    texto = bruto.decode("utf-8", errors="replace")
    if not texto.strip():
        return None
    if tipo is None or "json" in tipo.lower():
        try:
            return ler_json(texto)
        except ValueError:
            pass
    return texto


# --- limpar e comparar -----------------------------------------------------------

def limpar(corpo: Any, ignorar: list[str]) -> Any:
    """Cópia do corpo sem os campos de `ignorar` (caminhos com `/`; `*` casa lista ou objeto inteiro)."""
    copia = copy.deepcopy(corpo)
    for caminho in ignorar:
        _remover(copia, caminho.split("/"))
    return copia


def _remover(no: Any, partes: list[str]) -> None:
    cabeca, resto = partes[0], partes[1:]
    if isinstance(no, dict):
        chaves = list(no) if cabeca == "*" else ([cabeca] if cabeca in no else [])
        for chave in chaves:
            if resto:
                _remover(no[chave], resto)
            else:
                del no[chave]
    elif isinstance(no, list) and resto:
        if cabeca == "*":
            indices = range(len(no))
        elif cabeca.isdigit() and int(cabeca) < len(no):
            indices = [int(cabeca)]
        else:
            indices = []
        for i in indices:
            _remover(no[i], resto)


def _mostrar(valor: Any) -> str:
    return str(valor) if isinstance(valor, decimal.Decimal) else repr(valor)


def _folhas_iguais(a: Any, b: Any) -> bool:
    if type(a) is not type(b):
        return False
    if isinstance(a, decimal.Decimal):
        return a.as_tuple() == b.as_tuple() if a.is_finite() and b.is_finite() else str(a) == str(b)
    return a == b


def _diferencas(a: Any, b: Any, caminho: str, saida: list[str]) -> None:
    rotulo = caminho or "corpo"
    if isinstance(a, dict) and isinstance(b, dict):
        for chave in a:
            filho = f"{caminho}/{chave}" if caminho else str(chave)
            if chave not in b:
                saida.append(f"{filho}: ausente no local")
            else:
                _diferencas(a[chave], b[chave], filho, saida)
        for chave in b:
            if chave not in a:
                saida.append(f"{caminho}/{chave}: ausente no oficial" if caminho else f"{chave}: ausente no oficial")
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            saida.append(f"{rotulo}: tamanho {len(a)} != {len(b)}")
        for i in range(max(len(a), len(b))):
            filho = f"{caminho}/{i}" if caminho else str(i)
            if i >= len(b):
                saida.append(f"{filho}: ausente no local")
            elif i >= len(a):
                saida.append(f"{filho}: ausente no oficial")
            else:
                _diferencas(a[i], b[i], filho, saida)
    elif not _folhas_iguais(a, b):
        saida.append(f"{rotulo}: {_mostrar(a)} != {_mostrar(b)}")


def comparar(oficial: dict, local: dict, ignorar: list[str]) -> list[str]:
    """Diferenças entre duas respostas ({"status", "corpo"}), cada uma com o caminho. Vazia se iguais.

    O `tipo` (Content-Type) não entra: a conta local não tem cabeçalho.
    """
    saida: list[str] = []
    if oficial.get("status") != local.get("status"):
        saida.append(f"status: {oficial.get('status')} != {local.get('status')}")
    _diferencas(limpar(oficial.get("corpo"), ignorar), limpar(local.get("corpo"), ignorar), "", saida)
    return saida


def rodar(casos: list[dict], url_base: str, local: Callable[[dict], dict], ignorar: list[str]) -> dict:
    """Pergunta cada caso à calculadora oficial e à conta local e compara."""
    divergentes = []
    for caso in casos:
        requisicao = caso["requisicao"]
        oficial = chamar_oficial(url_base, requisicao)
        try:
            resposta_local = local(requisicao)
            diferencas = comparar(oficial, resposta_local, ignorar)
        except OficialIndisponivel:
            raise
        except Exception as erro:  # a conta local não pode derrubar a bateria inteira
            diferencas = [f"local falhou: {type(erro).__name__}: {erro}"]
        if diferencas:
            divergentes.append({"id": caso["id"], "diferencas": diferencas})
    return {"casos": len(casos), "iguais": len(casos) - len(divergentes), "divergentes": divergentes}


# --- linha de comando ------------------------------------------------------------------

def _ler_arquivo(caminho: Path, o_que: str) -> Any:
    if not caminho.exists():
        raise FileNotFoundError(f"Falta {o_que}: {caminho}")
    return ler_json(caminho.read_text(encoding="utf-8"))


def _carregar_modulo_da_bateria(caminho: Path):
    if not caminho.exists():
        raise FileNotFoundError(f"Falta a bateria local (bateria.py com responder(requisicao)): {caminho}")
    spec = importlib.util.spec_from_file_location("bateria_local", caminho)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def _carregar_bateria(caminho: Path) -> Callable[[dict], dict]:
    return _carregar_modulo_da_bateria(caminho).responder


def _casos_da_bateria(modulo, base_completa: str | None) -> list:
    return modulo.casos(Path(base_completa) if base_completa else None)


def _gravar_esperadas(pasta: Path, url: str, amostra: int | None = None, base_completa: str | None = None) -> int:
    meta = _ler_arquivo(pasta / "meta.json", "meta.json")
    if amostra is not None:
        modulo = _carregar_modulo_da_bateria(pasta / "bateria.py")
        if not (hasattr(modulo, "casos") and hasattr(modulo, "amostra")):
            raise FileNotFoundError(f"A bateria {pasta / 'bateria.py'} não tem casos() e amostra() para gravar a amostra")
        casos = modulo.amostra(_casos_da_bateria(modulo, base_completa), amostra)
    else:
        casos = _ler_arquivo(pasta / "casos-escritos.json", "casos-escritos.json")
    gravados = []
    for caso in casos:
        gravados.append({**{k: v for k, v in caso.items() if k != "resposta"},
                         "resposta": chamar_oficial(url, caso["requisicao"])})
    destino = pasta / "esperadas.json"
    texto = escrever_json({"meta": meta, "casos": gravados})
    temporario = destino.with_suffix(".json.tmp")
    temporario.write_text(texto, encoding="utf-8")
    temporario.replace(destino)
    print(f"{len(gravados)} casos gravados em {destino} ({len(texto.encode('utf-8'))} bytes)")
    return 0


def _comparar(pasta: Path, url: str, completa: bool, amostra: int | None, base_completa: str | None = None) -> int:
    config = _ler_arquivo(pasta / "config.json", "config.json")
    modulo = _carregar_modulo_da_bateria(pasta / "bateria.py")
    local = modulo.responder
    if completa and hasattr(modulo, "casos"):
        casos = _casos_da_bateria(modulo, base_completa)
    else:
        esperadas = _ler_arquivo(pasta / "esperadas.json", "esperadas.json (rode com --gravar-esperadas)")
        casos = esperadas["casos"]
        if not completa:
            n = AMOSTRA_PADRAO if amostra is None else amostra
            if n < len(casos):
                casos = random.Random(0).sample(casos, n)
    inicio = time.monotonic()
    relatorio = rodar(casos, url, local, config.get("ignorar", []))
    print(f"{relatorio['casos']} casos, {relatorio['iguais']} iguais, {time.monotonic() - inicio:.0f} s")
    for divergente in relatorio["divergentes"]:
        print(f"DIVERGE {divergente['id']}")
        for diferenca in divergente["diferencas"]:
            print(f"  {diferenca}")
    return 0 if not relatorio["divergentes"] else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Compara a conta local com a calculadora oficial.")
    ap.add_argument("--skill", required=True)
    ap.add_argument("--oficial", required=True, help="URL base da calculadora oficial, com o contexto (/api)")
    ap.add_argument("--gravar-esperadas", action="store_true",
                    help="grava esperadas.json com as respostas oficiais, sem comparar")
    ap.add_argument("--amostra", type=int, help=f"compara só N casos (padrão {AMOSTRA_PADRAO}); com --gravar-esperadas, grava a amostra de N casos da bateria")
    ap.add_argument("--completa", action="store_true", help="compara todos os casos")
    ap.add_argument("--base-completa", help="base de onde a bateria gera os casos (padrão: a da skill)")
    ap.add_argument("--raiz", default=str(ROOT), help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    pasta = Path(args.raiz) / "conferencia" / args.skill
    try:
        if args.gravar_esperadas:
            return _gravar_esperadas(pasta, args.oficial, args.amostra, args.base_completa)
        return _comparar(pasta, args.oficial, args.completa, args.amostra, args.base_completa)
    except (OficialIndisponivel, FileNotFoundError) as erro:
        print(f"erro: {erro}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
