#!/usr/bin/env python3
"""Linha de comando da skill cbs-ibs-regime-regular (CBS e IBS no regime regular).

Resultado gerencial: simulação com a conta da calculadora oficial da Receita Federal (a skill não é um
aplicativo da Receita; confira com o contador). Todo resultado de 2027 em diante é simulado: as alíquotas
são cenário ou informadas.

Uso:
    python3 calc.py operacao.json [--porta tecnica] [--cenario em-2027|reforma-completa]
    cat operacao.json | python3 calc.py
    python3 calc.py --cenarios
    python3 calc.py --erro REG-001
    python3 calc.py --versao
    python3 calc.py --checar-versao

Porta em português (padrão). A operação é:
    {"id": "pedido-1",                      opcional
     "documento": "NF-e" | "NFC-e" | 55 | 65,   opcional (sem documento, a conta não pede tipo)
     "uf": "SP", "municipio": 3550308,      município pelo código IBGE
     "data": "2027-01-01",                  AAAA-MM-DD ou DD/MM/AAAA; padrão 2027-01-01
     "itens": [{"numero": 1,                opcional (padrão: a posição, de 1)
                "descricao": "...",         só para a pessoa, não vai para a conta
                "ncm": "84713012",          como veio (NCM de 7 dígitos não é corrigido)
                "cst": "000", "classificacao": "000001",
                "valor": 100.00,            base de cálculo
                "quantidade": 10, "unidade": "LT",  quantidade e unidade do item (copiadas para o bloco do IS)
                "aliquotas": {"cbs": 8.8, "ibs_estadual": 8.85, "ibs_municipal": 8.85,
                              "imposto_seletivo": {"ad_valorem": 0, "ad_rem": 0}},   opcional
                "imposto_seletivo": {"cst": "000", "classificacao": "000001", "valor": 100.00,
                                     "quantidade": 10, "unidade": "LT"}}]}   o bloco do IS
Item com Imposto Seletivo leva dois blocos: `aliquotas.imposto_seletivo` (alíquota ad valorem e ad rem) e o
`imposto_seletivo` do item, com a tributação do IS: `cst` e `classificacao` (a classificação tributária do IS),
`valor` (a base de cálculo do IS), `quantidade` e `unidade` (a unidade tributável, para o IS ad rem) e, se
for o caso, `imposto_informado`. Sem `quantidade` ou `unidade` no bloco, vale a do item; o que está no bloco
nunca é trocado. Sem o bloco, a calculadora responde com o erro oficial (REG-013).
                (o nome da Receita também vale: ibsEstadual, ibsMunicipal, impostoSeletivo, adValorem, adRem)
Sem `aliquotas`, de 2027 em diante o item leva as do cenário (`--cenario`, padrão em-2027); antes de 2027
nunca leva (a calculadora recusa alíquota nominal em 2026, REG-036). Alíquota informada vence o cenário.
`aliquotas` parcial (só `cbs`, por exemplo) vai como veio, sem completar com o cenário: a calculadora pode
responder com o erro oficial (REG-037).
Campos com o nome da Receita (baseCalculo, cClassTrib, aliquotasNominais, impostoSeletivo, ...) passam como
vieram. Não há alíquota padrão de Imposto Seletivo.

Porta técnica (`--porta tecnica`): o JSON do POST /calculadora/observabilidade/regime-geral da Receita, rodado
como veio (sem alíquota do cenário), depois da checagem de escopo.

Fora do escopo, recusado aqui antes da conta: tpDoc que não seja 55 nem 65 (NFS-e e outros), item com NBS
e gCompraGov.

Só biblioteca padrão (Python 3.10+).
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
import urllib.request
from decimal import Decimal
from pathlib import Path

_AQUI = Path(__file__).resolve().parent
if str(_AQUI) not in sys.path:
    sys.path.append(str(_AQUI))

try:  # no GPT, os rr_*.py vêm juntos no rr_pacote.py (gerado pelo sync); no plugin, ficam soltos
    import rr_pacote  # noqa: F401,E402
except ModuleNotFoundError as erro:
    if erro.name != "rr_pacote":
        raise
import rr_base  # noqa: E402  (precisa do caminho acima)
import rr_erros  # noqa: E402
import rr_motor  # noqa: E402

ROTAS = {
    ("POST", "/calculadora/regime-geral"): rr_motor.regime_geral,
    ("POST", "/calculadora/observabilidade/regime-geral"): rr_motor.observabilidade,
}
ROTA_OBSERVABILIDADE = ("POST", "/calculadora/observabilidade/regime-geral")

# No pacote, references/ fica ao lado de scripts/; no GPT, o upload é plano e o arquivo fica ao lado deste.
ARQUIVO_ALIQUOTAS = next((p for p in (_AQUI.parent / "references" / "aliquotas.json", _AQUI / "aliquotas.json")
                          if p.is_file()), _AQUI.parent / "references" / "aliquotas.json")
DATA_PADRAO = "2027-01-01"
CENARIO_PADRAO = "em-2027"
VERSAO_OPERACAO = "1.0.0"
AVISO_ORIGEM = ("Esta skill não é um aplicativo da Receita Federal: usa a conta da calculadora oficial dela. "
                "Resultado gerencial, confira com o contador.")

# Entra só no campo `type` dos corpos de erro: aponta para a documentação da Receita, nunca para um endereço local.
BASE_URL_CLI = "https://github.com/ReceitaFederal/calculadora-rtc-regime-regular"

URL_VERSAO = "https://api.github.com/repos/ReceitaFederal/calculadora-rtc-regime-regular/releases/latest"
URL_TABELAS = ("https://api.github.com/repos/ReceitaFederal/calculadora-rtc-regime-regular/commits"
               "?path=flyway/sql&per_page=1")
USER_AGENT = "cd2-super-skill"
_LIMITE_RESPOSTA = 1_000_000

# Troca de teste: os testes substituem esta função e nunca tocam a rede.
_abrir = urllib.request.urlopen

_BASE = None


def _base():
    global _BASE
    if _BASE is None:
        _BASE = rr_base.Base()
    return _BASE


# ------------------------------------------------------------ o que a bateria chama

def executar_completo(requisicao: dict, base=None, base_url: str = rr_motor.BASE_URL_PADRAO, agora=None) -> tuple:
    """(status, corpo, cabeçalhos) da conta local para uma requisição à calculadora oficial (caminho sem /api)."""
    rota = (requisicao.get("metodo", "GET").upper(), requisicao["caminho"])
    if rota not in ROTAS:
        raise ValueError(f"rota sem port: {rota[0]} {rota[1]}")
    return ROTAS[rota](requisicao.get("corpo"), base or _base(), base_url, agora=agora)


def executar(requisicao: dict, base=None, base_url: str = rr_motor.BASE_URL_PADRAO, agora=None) -> dict:
    """A resposta local ({"status", "corpo"}) de uma requisição à calculadora oficial (caminho sem o /api)."""
    status, corpo, _cabecalhos = executar_completo(requisicao, base, base_url, agora)
    return {"status": status, "corpo": corpo}


# ----------------------------------------------------------------- alíquotas

def ler_aliquotas() -> dict:
    """references/aliquotas.json, com os números como Decimal."""
    with open(ARQUIVO_ALIQUOTAS, encoding="utf-8") as arquivo:
        return json.load(arquivo, parse_float=Decimal)


class Recusa(ValueError):
    """A operação está fora do escopo da skill: a mensagem é para a pessoa, e a conta nem começa."""


# --------------------------------------------------------------- normalização

_RE_DATA_ISO = re.compile(r"(\d{4})-(\d{2})-(\d{2})(?:T.*)?")
_RE_DATA_BR = re.compile(r"(\d{2})/(\d{2})/(\d{4})")
_DOCUMENTOS = {"nfe": 55, "nf-e": 55, "55": 55, "nfce": 65, "nfc-e": 65, "65": 65}
_NFSE = ("nfse", "nfs-e")
_ITEM_ALIAS = {"valor": "baseCalculo", "classificacao": "cClassTrib", "aliquotas": "aliquotasNominais",
               "imposto_seletivo": "impostoSeletivo"}
_ITEM_SO_PARA_A_PESSOA = ("descricao",)
_ALIQUOTAS_ALIAS = {"ibs_estadual": "ibsEstadual", "ibs_municipal": "ibsMunicipal",
                    "imposto_seletivo": "impostoSeletivo"}
_ALIQUOTAS_IS_ALIAS = {"ad_valorem": "adValorem", "ad_rem": "adRem"}
_IS_ITEM_ALIAS = {"valor": "baseCalculo", "classificacao": "cClassTrib", "imposto_informado": "impostoInformado"}


def _renomear(bloco, alias):
    """O bloco com as chaves em português trocadas pelas da Receita; o que já vem com o nome dela passa."""
    if not isinstance(bloco, dict):
        return bloco
    return {alias.get(chave, chave): valor for chave, valor in bloco.items()}


def _aliquotas_receita(bloco):
    novo = _renomear(bloco, _ALIQUOTAS_ALIAS)
    if isinstance(novo, dict) and "impostoSeletivo" in novo:
        novo["impostoSeletivo"] = _renomear(novo["impostoSeletivo"], _ALIQUOTAS_IS_ALIAS)
    return novo
_TPDOC_ACEITOS = (55, 65)


def _data_iso(texto):
    """AAAA-MM-DD de um texto de data (ISO ou DD/MM/AAAA), ou None."""
    if not isinstance(texto, str):
        return None
    achou = _RE_DATA_ISO.fullmatch(texto.strip())
    if achou:
        return f"{achou.group(1)}-{achou.group(2)}-{achou.group(3)}"
    achou = _RE_DATA_BR.fullmatch(texto.strip())
    if achou:
        return f"{achou.group(3)}-{achou.group(2)}-{achou.group(1)}"
    return None


def _tp_doc(valor):
    """O tpDoc de um valor de documento em português ou numérico; levanta Recusa fora de 55/65."""
    if isinstance(valor, bool):
        chave = None
    elif isinstance(valor, (int, Decimal)):
        chave = str(int(valor)) if valor == int(valor) else None
    else:
        chave = str(valor).strip().lower().replace(" ", "")
    if chave in _DOCUMENTOS:
        return _DOCUMENTOS[chave]
    if chave in _NFSE:
        raise Recusa("NFS-e fica fora desta skill: ela calcula só NF-e (55) e NFC-e (65).")
    raise Recusa(f"Tipo de documento {valor!r} fora do escopo: esta skill calcula só NF-e (55) e NFC-e (65).")


def _ano(dh) -> int | None:
    dia = _data_iso(dh)
    return int(dia[:4]) if dia else None


def verificar_escopo(operacao: dict) -> None:
    """Recusa, antes da conta, o que a skill não calcula: tpDoc fora de 55/65, NBS e gCompraGov. Levanta Recusa.

    `"nbs": null` vale como ausente, e `"gCompraGov": null` também."""
    if not isinstance(operacao, dict):
        return
    tp = operacao.get("tpDoc")
    if tp is not None:
        if isinstance(tp, str):
            texto = tp.strip()
            tp = int(texto) if texto.isdigit() else tp
        if isinstance(tp, bool) or not isinstance(tp, (int, Decimal)) or tp not in _TPDOC_ACEITOS:
            _tp_doc(tp)  # NFS-e ou outro: levanta com a mensagem certa
            raise Recusa(f"Tipo de documento {tp!r} fora do escopo: esta skill calcula só NF-e (55) e NFC-e (65).")
    if operacao.get("gCompraGov") is not None:
        raise Recusa("Compra governamental (gCompraGov) fica fora desta skill: ela não calcula o "
                     "redutor de compras públicas.")
    itens = operacao.get("itens")
    if isinstance(itens, list):
        for i, item in enumerate(itens):
            if isinstance(item, dict) and item.get("nbs") is not None:
                raise Recusa(f"O item {i + 1} traz NBS (serviço): esta skill calcula só mercadoria, com NCM. "
                             "Serviço (NFS-e) fica de fora.")


def _aliquotas_do_cenario(cenario: str) -> dict:
    dados = ler_aliquotas()
    escolhido = dados["cenarios"].get(cenario)
    if escolhido is None:
        raise Recusa(f"Cenário {cenario!r} não existe. Use um de: {', '.join(dados['cenarios'])}.")
    return {"cbs": escolhido["cbs"], "ibsEstadual": escolhido["ibs_estadual"],
            "ibsMunicipal": escolhido["ibs_municipal"]}


def _item_receita(item, posicao: int):
    if not isinstance(item, dict):
        return item
    saida = {}
    for chave, valor in item.items():
        if chave in _ITEM_SO_PARA_A_PESSOA:
            continue
        saida[_ITEM_ALIAS.get(chave, chave)] = valor
    if "aliquotasNominais" in saida:
        saida["aliquotasNominais"] = _aliquotas_receita(saida["aliquotasNominais"])
    bloco_is = saida.get("impostoSeletivo")
    if isinstance(bloco_is, dict):
        bloco_is = _renomear(bloco_is, _IS_ITEM_ALIAS)
        for campo in ("quantidade", "unidade"):
            if campo not in bloco_is and campo in saida:
                bloco_is[campo] = saida[campo]
        saida["impostoSeletivo"] = bloco_is
    saida.setdefault("numero", posicao)
    if "ncm" in saida and isinstance(saida["ncm"], (int, Decimal)) and not isinstance(saida["ncm"], bool):
        saida["ncm"] = str(saida["ncm"])
    return saida


def normalizar(entrada: dict, cenario: str = CENARIO_PADRAO) -> dict:
    """A operação em português vira o JSON da Receita (ver a docstring do módulo). Levanta Recusa.

    Não corrige nada: NCM e códigos passam como vieram. Alíquota do cenário só de 2027 em diante e só no
    item sem `aliquotas`; a informada vence."""
    return _montar(entrada, cenario)[0]


def _montar(entrada, cenario):
    """(operação da Receita, se alguma alíquota veio do cenário)."""
    padrao = _aliquotas_do_cenario(cenario)  # cenário inexistente é recusado mesmo antes de 2027
    if not isinstance(entrada, dict):
        raise Recusa("A operação precisa ser um objeto JSON.")
    operacao = {"id": entrada.get("id", "operacao-1"), "versao": VERSAO_OPERACAO}
    data = entrada.get("data")
    if "dhFatoGerador" in entrada:
        operacao["dhFatoGerador"] = entrada["dhFatoGerador"]
    else:
        dia = DATA_PADRAO if data is None else (_data_iso(data) or data)
        operacao["dhFatoGerador"] = f"{dia}T12:00:00-03:00" if _data_iso(dia) else dia
    if "municipio" in entrada:
        municipio = entrada["municipio"]
        operacao["municipio"] = int(municipio) if isinstance(municipio, str) and municipio.strip().isdigit() \
            else municipio
    if "uf" in entrada:
        operacao["uf"] = entrada["uf"]
    documento = entrada.get("documento", entrada.get("tpDoc"))
    if documento is not None:
        operacao["tpDoc"] = _tp_doc(documento)
    if "gCompraGov" in entrada:
        operacao["gCompraGov"] = entrada["gCompraGov"]
    itens = entrada.get("itens")
    if isinstance(itens, list):
        operacao["itens"] = [_item_receita(item, i + 1) for i, item in enumerate(itens)]
    elif itens is not None:
        operacao["itens"] = itens
    verificar_escopo(operacao)
    ano = _ano(operacao["dhFatoGerador"])
    usou = False
    if ano is not None and ano >= 2027 and isinstance(operacao.get("itens"), list):
        for item in operacao["itens"]:
            if isinstance(item, dict) and item.get("aliquotasNominais") is None:
                item["aliquotasNominais"] = copy.deepcopy(padrao)
                usou = True
    return operacao, usou


# ------------------------------------------------------------------ a conta

def _versao_resumida(base) -> dict:
    meta = base.meta()
    meta = meta if isinstance(meta, dict) else {}
    calculadora = meta.get("calculadora")
    conferencia = meta.get("conferencia")
    return {"calculadora": calculadora.get("versao", "") if isinstance(calculadora, dict) else "",
            "base": base.versao_base()["versao"],
            "conferida_em": conferencia.get("data", "") if isinstance(conferencia, dict) else ""}


def calcular(operacao: dict, base, base_url: str = BASE_URL_CLI, agora=None) -> dict:
    """Roda a operação no formato da Receita (já normalizada) pela observabilidade e monta a saída da CLI."""
    status, corpo, cabecalhos = executar_completo(
        {"metodo": "POST", "caminho": ROTA_OBSERVABILIDADE[1], "corpo": operacao}, base, base_url, agora)
    ano = _ano(operacao.get("dhFatoGerador")) if isinstance(operacao, dict) else None
    simulado = ("x-warning-dados-simulados" in cabecalhos) or (ano is not None and ano >= 2027)
    saida = {"status": status, "corpo": corpo, "versao": _versao_resumida(base), "simulado": simulado,
             "aviso_origem": AVISO_ORIGEM}
    if simulado:
        saida["aviso"] = ("Resultado simulado: as alíquotas de 2027 em diante são cenário ou informadas, "
                          "não as definitivas. Confira com o contador.")
    return saida


def executar_operacao(entrada: dict, base, porta: str = "pt", cenario: str = CENARIO_PADRAO,
                      base_url: str = BASE_URL_CLI, agora=None) -> dict:
    """O que a linha de comando faz com uma operação. Levanta Recusa fora do escopo."""
    if porta == "tecnica":
        verificar_escopo(entrada)
        operacao, cenario_usado = entrada, None
    else:
        operacao, usou = _montar(entrada, cenario)
        cenario_usado = None
        if usou:
            dados = ler_aliquotas()
            cenario_usado = {"cenario": cenario, **dados["cenarios"][cenario], "checked_at": dados["checked_at"],
                             "fontes": dados["fontes"]}
    saida = calcular(operacao, base, base_url, agora)
    if cenario_usado is not None:
        saida["cenario"] = cenario_usado
    return saida


# --------------------------------------------------------- aviso de versão

def _partes(versao: str):
    achou = re.fullmatch(r"[vV]?(\d+(?:\.\d+)*)(?:[-+].*)?", versao.strip())
    if achou is None:
        return None
    partes = [int(p) for p in achou.group(1).split(".")]
    while len(partes) > 1 and partes[-1] == 0:
        partes.pop()
    return tuple(partes)


def _pedir_json(url: str, prazo: float):
    pedido = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
    with _abrir(pedido, timeout=prazo) as resposta:
        return json.loads(resposta.read(_LIMITE_RESPOSTA).decode("utf-8"))


_RE_DIA = re.compile(r"(\d{4}-\d{2}-\d{2})(?:T.*)?")
_RE_COMMIT = re.compile(r"[0-9a-f]{40}")


def _tabela_nova(meta: dict, prazo: float):
    """True ou False se a consulta das tabelas fechou; None se não deu para saber. Nunca levanta exceção.

    O último commit de flyway/sql da Receita é novo quando é outro commit, e não o espelhado, e tem data
    depois da última conferência (sem ela, vale a data da base)."""
    try:
        corpo = _pedir_json(URL_TABELAS, prazo)
        ultimo = corpo[0]
        sha = ultimo["sha"]
        dia = _RE_DIA.fullmatch(ultimo["commit"]["committer"]["date"])
        if not isinstance(sha, str) or not _RE_COMMIT.fullmatch(sha) or dia is None:
            return None
    except Exception:
        return None
    try:
        if sha == meta["migracoes"]["commit"]:
            return False
        conferencia = meta.get("conferencia") if isinstance(meta.get("conferencia"), dict) else {}
        referencia = conferencia.get("data") or meta["base"]["data"]
        if not isinstance(referencia, str) or _RE_DIA.fullmatch(referencia) is None:
            return None
    except Exception:
        return None
    return dia.group(1) > referencia[:10]


def checar_versao(meta: dict, prazo: float = 4.0) -> dict:
    """Pergunta ao GitHub qual é a última release da calculadora da Receita e quando as tabelas mudaram
    pela última vez. Nunca levanta exceção.

    Só as duas URLs fixas e um User-Agent genérico saem daqui: nada da pessoa nem da operação."""
    resultado = {"consultado": False, "ha_versao_nova": None, "ha_tabela_nova": None, "calculadora": None,
                 "mensagem": ""}
    try:
        corpo = _pedir_json(URL_VERSAO, prazo)
        tag = corpo["tag_name"]
        ultima = _partes(tag)
        if ultima is None:
            raise ValueError("tag fora do padrão")
    except Exception:
        resultado["mensagem"] = ("Não consegui consultar a versão mais nova agora. "
                                 "A conta continua valendo com a versão que está na skill.")
        return resultado
    resultado["consultado"] = True
    resultado["calculadora"] = tag
    espelhada = None
    try:
        espelhada = _partes(meta["calculadora"]["versao"])
    except Exception:
        pass
    base = None
    try:
        base = meta["base"]["versao"] or None
    except Exception:
        pass
    tabelas = f"as da base {base}" if base else "as que vêm na skill"
    if espelhada is None:
        mensagem = f"A versão mais nova da calculadora da Receita é a {tag}. Não sei qual é a da skill."
    elif ultima > espelhada:
        resultado["ha_versao_nova"] = True
        mensagem = (f"A Receita publicou a versão {tag} da calculadora e a skill ainda usa a "
                    f"{meta['calculadora']['versao']}. Peça a atualização da skill e confira com o contador.")
    else:
        resultado["ha_versao_nova"] = False
        mensagem = (f"O código da calculadora na skill é o mais novo da Receita ({tag}). "
                    f"As tabelas em uso são {tabelas}.")
    nova = _tabela_nova(meta, prazo)
    resultado["ha_tabela_nova"] = nova
    if nova is True:
        mensagem += (" A Receita publicou tabelas novas depois dessas, e a atualização do plugin traz essas "
                     "tabelas.")
    elif nova is None:
        mensagem += " Não consegui conferir agora se a Receita publicou tabelas novas."
    resultado["mensagem"] = mensagem
    return resultado


# --------------------------------------------------------------------- CLI

def _fail(errors) -> int:
    print(json.dumps({"errors": errors}, ensure_ascii=False, indent=2))
    return 2


def _recusar_constante(nome):
    raise ValueError(f"valor {nome} não é permitido em JSON")


def _imprimir(valor) -> None:
    print(json.dumps(valor, ensure_ascii=False, indent=2, default=str))


def _executar_versao(base) -> dict:
    meta = base.meta()
    publicada = base.versao_base()
    corpo = {"versaoApp": meta["calculadora"]["versao"], "versaoDb": publicada["versao"],
             "descricaoVersaoDb": publicada["descricao"],
             "dataVersaoDb": publicada["data"].isoformat() if hasattr(publicada["data"], "isoformat")
             else str(publicada["data"])}
    for campo in ("arquivoDb", "ambiente"):
        if meta["calculadora"].get(campo) is not None:
            corpo[campo] = meta["calculadora"][campo]
    return {"status": 200, "corpo": corpo, "versao": _versao_resumida(base)}


def _descrever_erro(codigo: str) -> dict:
    achado = rr_erros.descrever_erro(codigo)
    if achado is None:
        return {"status": 404, "corpo": {"detail": f"Erro de código {codigo} não encontrado."}}
    return {"status": 200, "corpo": achado}


def _parser():
    parser = argparse.ArgumentParser(
        description=("Calculadora de CBS e IBS no regime regular, com a conta da calculadora oficial da Receita "
                     "Federal. Resultado simulado e gerencial: a skill não é um aplicativo da Receita Federal, "
                     "confira com o contador."),
        epilog=("exemplos:\n"
                "  calcular uma operação:   python3 calc.py operacao.json\n"
                "  reforma completa:        python3 calc.py operacao.json --cenario reforma-completa\n"
                "  JSON da Receita:         python3 calc.py operacao.json --porta tecnica\n"
                "  alíquotas dos cenários:  python3 calc.py --cenarios\n"
                "  explicar um erro:        python3 calc.py --erro REG-001\n"
                "  versões da skill:        python3 calc.py --versao\n"
                "  há versão nova?          python3 calc.py --checar-versao"),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("operacao", nargs="?", help="arquivo JSON da operação (padrão: entrada padrão)")
    parser.add_argument("--porta", choices=("pt", "tecnica"), default="pt",
                        help="pt: operação em português (padrão); tecnica: JSON da observabilidade da Receita")
    parser.add_argument("--cenario", default=CENARIO_PADRAO,
                        help="cenário de alíquotas de 2027 em diante: em-2027 (padrão) ou reforma-completa")
    modo = parser.add_mutually_exclusive_group()
    modo.add_argument("--cenarios", action="store_true", help="mostra as alíquotas dos cenários (aliquotas.json)")
    modo.add_argument("--erro", metavar="REG-001", help="explica um código de erro")
    modo.add_argument("--versao", action="store_true", help="mostra as versões da calculadora e da base")
    modo.add_argument("--checar-versao", action="store_true",
                      help="consulta o GitHub da Receita e diz se há versão nova (única ida à internet)")
    return parser


def _ler_operacao(caminho):
    """Devolve (operação, None) ou (None, mensagem de erro)."""
    try:
        if caminho:
            with open(caminho, encoding="utf-8-sig") as fh:
                bruto = fh.read()
        else:
            bruto = sys.stdin.read()
    except OSError as exc:
        return None, f"Não consegui ler o arquivo de entrada: {exc.strerror or exc}."
    except ValueError:
        return None, "A entrada não está em UTF-8."
    try:
        return json.loads(bruto.lstrip("﻿"), parse_float=Decimal, parse_constant=_recusar_constante), None
    except ValueError as exc:
        return None, f"JSON inválido: {exc}."


def main(argv=None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    consulta = args.cenarios or args.erro is not None or args.versao or args.checar_versao
    if consulta and args.operacao:
        parser.error("as consultas não levam arquivo de operação")
    for stream in (sys.stdin, sys.stdout):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        if args.cenarios:
            _imprimir(ler_aliquotas())
            return 0
        if args.checar_versao:
            try:
                with rr_base.Base() as base:
                    meta = base.meta()
            except Exception:  # esta consulta nunca derruba: sem meta, só informa a última versão
                meta = {}
            _imprimir(checar_versao(meta))
            return 0
        entrada = None
        if args.erro is None and not args.versao:
            entrada, erro = _ler_operacao(args.operacao)
            if erro:
                return _fail([erro])
        with rr_base.Base() as base:
            if args.erro is not None:
                saida = _descrever_erro(args.erro)
            elif args.versao:
                saida = _executar_versao(base)
            else:
                try:
                    saida = executar_operacao(entrada, base, args.porta, args.cenario)
                except Recusa as recusa:
                    return _fail([str(recusa)])
        texto = json.dumps(saida, ensure_ascii=False, indent=2, default=str)
    except rr_base.BaseIndisponivel as exc:
        return _fail([f"A base da Receita não está disponível: {exc}"])
    except Exception as exc:  # rede de segurança: o motor responde com status, não levanta
        return _fail([f"Erro inesperado ao calcular: {type(exc).__name__}."])
    print(texto)
    return 0


if __name__ == "__main__":
    sys.exit(main())
