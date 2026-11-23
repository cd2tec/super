#!/usr/bin/env python3
"""Linha de comando da skill cbs-ibs-simples (calculadora do Simples Nacional, IBS e CBS).

Resultado gerencial. Esta skill não é um aplicativo da Receita Federal: confira com o contador.

Uso:
    python3 calc.py --input operacao.json
    cat operacao.json | python3 calc.py --humano
    python3 calc.py --atividades 2027-01-01 --anexo I --anexo V
    python3 calc.py --atividade 01 --data 2027-01-01
    python3 calc.py --erro VSN-001
    python3 calc.py --versao
    python3 calc.py --checar-versao

So biblioteca padrao (Python 3.10+).
"""
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

import simples_base  # noqa: E402
import simples_motor  # noqa: E402

# Entra so no campo `type` dos corpos de erro. Na oficial esse campo traz o endereco do servidor que
# respondeu; aqui nao existe servidor, entao ele aponta para a documentacao da Receita Federal e
# nunca para um endereco local.
BASE_URL_CLI = "https://github.com/ReceitaFederal/calculadora-rtc-simples-nacional"

URL_VERSAO = "https://api.github.com/repos/ReceitaFederal/calculadora-rtc-simples-nacional/releases/latest"
URL_TABELAS = ("https://api.github.com/repos/ReceitaFederal/calculadora-rtc-regime-regular/commits"
               "?path=flyway/sql&per_page=1")
USER_AGENT = "cd2-super-skill"
_LIMITE_RESPOSTA = 1_000_000

# Troca de teste: os testes substituem esta funcao e nunca tocam a rede.
_abrir = urllib.request.urlopen


# ------------------------------------------------------------- normalizacao

_CAMPOS_TEXTO_OPERACAO = ("id", "versao", "data", "tpDoc", "CRT", "tpRegimeIBSCBS", "opSimpNac",
                          "regApIBSCBSSN", "regApTribSN", "tpEnteGov")
_CAMPOS_DECIMAIS_ITEM = ("baseCalculo", "valorImpostoSeletivo")
_CAMPOS_CODIGO_ITEM = ("cAtivSN", "cClassTrib")
_CAMPOS_NOMENCLATURA = ("ncm", "NCM", "nbs", "NBS")
_ANEXOS = ("anexoI", "anexoII", "anexoIII", "anexoIV", "anexoV")

_RE_DATA_BR = re.compile(r"(\d{2})/(\d{2})/(\d{4})")


def _e_numero(valor) -> bool:
    return isinstance(valor, (int, Decimal)) and not isinstance(valor, bool)


_EXPOENTE_MAXIMO = 1000  # acima disso o número não é expandido: fica para o motor recusar


def _numero_em_texto(valor):
    """Texto do número JSON, sem notação científica. None quando expandir seria absurdo."""
    if isinstance(valor, Decimal):
        if not valor.is_finite() or abs(valor.as_tuple().exponent) > _EXPOENTE_MAXIMO:
            return None
        return format(valor, "f")
    return str(valor)


_RE_NUMERICO = re.compile(r"[+-]?\d[\d.,]*")
# parte inteira sem zero a esquerda: "0.022" nunca e milhar, e o formato das aliquotas oficiais
_RE_MILHAR_EM_DUVIDA = re.compile(r"[+-]?[1-9]\d*[.,]\d{3}")


def _decimal_em_formato_oficial(texto: str) -> str:
    """Troca so o formato: 1.000,00 vira 1000.00 e 2,35 vira 2.35. Ambiguo ou nao numero fica como está."""
    tem_ponto, tem_virgula = "." in texto, "," in texto
    if tem_ponto and tem_virgula:
        decimal = "," if texto.rfind(",") > texto.rfind(".") else "."
        milhar = "." if decimal == "," else ","
        # primeiro grupo de 1 a 3 digitos sem zero a esquerda, depois grupos de exatamente 3
        padrao = rf"[+-]?[1-9]\d{{0,2}}(?:{re.escape(milhar)}\d{{3}})+{re.escape(decimal)}\d+"
        if re.fullmatch(padrao, texto):
            return texto.replace(milhar, "").replace(decimal, ".")
    elif tem_virgula and re.fullmatch(r"[+-]?\d+,\d+", texto):
        return texto.replace(",", ".")
    return texto


def _aviso_milhar(caminho: str, texto: str) -> str:
    lido = texto.replace(".", ",")
    return (f'Em {caminho}, "{texto}" foi lido como {lido}, e não como milhar. '
            "Se era milhar, escreva sem o ponto.")


def _so_digitos(texto: str) -> str:
    if re.fullmatch(r"\d+(?:[. \-]\d+)+", texto):
        return re.sub(r"\D", "", texto)
    return texto


def _normalizar_campo(registro: dict, nome: str, caminho: str, tipo: str, avisos: list):
    valor = registro[nome]
    if _e_numero(valor):
        novo = _numero_em_texto(valor)
        if novo is None:
            return
        registro[nome] = novo
        if novo == str(valor):
            avisos.append(f'Em {caminho}, o número {valor} virou o texto "{novo}".')
        else:
            avisos.append(f'{caminho}: troquei "{valor}" por "{novo}" (número do JSON virou texto).')
        return
    if not isinstance(valor, str):
        return
    novo, extra = valor, []
    if tipo == "decimal":
        aparado = valor.strip()
        if _RE_NUMERICO.fullmatch(aparado):
            novo = _decimal_em_formato_oficial(aparado)
            if _RE_MILHAR_EM_DUVIDA.fullmatch(aparado):
                extra.append(_aviso_milhar(caminho, aparado))
    elif tipo == "nomenclatura":
        novo = _so_digitos(valor)
    elif tipo == "data":
        achou = _RE_DATA_BR.fullmatch(valor)
        if achou:
            novo = f"{achou.group(3)}-{achou.group(2)}-{achou.group(1)}"
    motivo = {"decimal": "formato do número", "nomenclatura": "só os dígitos",
              "data": "data no formato AAAA-MM-DD"}.get(tipo, "")
    if novo != valor:
        registro[nome] = novo
        avisos.append(f'{caminho}: troquei "{valor}" por "{novo}" ({motivo}).')
    avisos.extend(extra)


def normalizar(operacao):
    """Muda formato, nunca conteúdo: vírgula decimal, milhar, número JSON em texto, NCM e NBS só com
    dígitos e data DD/MM/AAAA. Devolve (operação nova, avisos). Não inventa campo e não mexe na entrada."""
    if not isinstance(operacao, dict):
        return operacao, []
    nova = copy.deepcopy(operacao)
    avisos = []
    for nome in _CAMPOS_TEXTO_OPERACAO:
        if nome in nova:
            _normalizar_campo(nova, nome, nome, "data" if nome == "data" else "texto", avisos)
    aliquotas = nova.get("aliquotasEfetivas")
    if isinstance(aliquotas, dict):
        for anexo in _ANEXOS:
            bloco = aliquotas.get(anexo)
            if not isinstance(bloco, dict):
                continue
            for nome in ("pCBSSN", "pIBSSN"):
                if nome in bloco:
                    _normalizar_campo(bloco, nome, f"aliquotasEfetivas.{anexo}.{nome}", "decimal", avisos)
            sem = bloco.get("semRedistribuicaoISS")
            if isinstance(sem, dict):
                for nome in ("pCBSSN", "pIBSSN"):
                    if nome in sem:
                        _normalizar_campo(sem, nome, f"aliquotasEfetivas.{anexo}.semRedistribuicaoISS.{nome}",
                                          "decimal", avisos)
    itens = nova.get("itens")
    if isinstance(itens, list):
        for i, item in enumerate(itens):
            if not isinstance(item, dict):
                continue
            base = f"itens[{i}]."
            for nome in _CAMPOS_DECIMAIS_ITEM:
                if nome in item:
                    _normalizar_campo(item, nome, base + nome, "decimal", avisos)
            for nome in _CAMPOS_CODIGO_ITEM:
                if nome in item:
                    _normalizar_campo(item, nome, base + nome, "texto", avisos)
            for nome in _CAMPOS_NOMENCLATURA:
                if nome in item:
                    _normalizar_campo(item, nome, base + nome, "nomenclatura", avisos)
    return nova, avisos


# ------------------------------------------------------------------ saída

def _versao_resumida(base) -> dict:
    meta = base.meta()
    meta = meta if isinstance(meta, dict) else {}
    calculadora = meta.get("calculadora")
    conferencia = meta.get("conferencia")
    return {"calculadora": calculadora.get("versao", "") if isinstance(calculadora, dict) else "",
            "base": base.versao_base()["versao"],
            "conferida_em": conferencia.get("data", "") if isinstance(conferencia, dict) else ""}


def _resposta(status, corpo, base) -> dict:
    return {"status": status, "corpo": corpo, "versao": _versao_resumida(base)}


def executar_operacao(operacao, base, humano=False, base_url=BASE_URL_CLI, agora=None) -> dict:
    """O que a linha de comando faz com uma operação. `base_url` só muda o `type` dos erros."""
    avisos = []
    if humano:
        operacao, avisos = normalizar(operacao)
    status, corpo = simples_motor.calcular(operacao, base, base_url, agora=agora)
    saida = _resposta(status, corpo, base)
    if humano:
        saida["normalizacoes"] = avisos
    return saida


def _iso(valor) -> str:
    return valor.isoformat() if hasattr(valor, "isoformat") else str(valor)


def _executar_versao(base) -> dict:
    meta = base.meta()
    publicada = base.versao_base()
    corpo = {"versaoApp": meta["calculadora"]["versao"], "versaoDb": publicada["versao"],
             "descricaoVersaoDb": publicada["descricao"], "dataVersaoDb": _iso(publicada["data"])}
    # VersaoDomain é NON_NULL: o arquivo e o ambiente da instalação oficial só saem se o meta os traz
    for campo in ("arquivoDb", "ambiente"):
        if meta["calculadora"].get(campo) is not None:
            corpo[campo] = meta["calculadora"][campo]
    return _resposta(200, corpo, base)


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

    O último commit da pasta de migrações da Receita é novo quando é outro commit, e não o espelhado, e
    tem data depois da última conferência (a conferência usa as migrações daquele dia; sem ela, vale a
    data da base)."""
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
    print(json.dumps(valor, ensure_ascii=False, indent=2))


def _parser():
    parser = argparse.ArgumentParser(
        description=("Calculadora do Simples Nacional (IBS e CBS) com a conta da calculadora oficial da "
                     "Receita Federal. Resultado gerencial: a skill não é um aplicativo da Receita "
                     "Federal, confira com o contador."),
        epilog=("exemplos:\n"
                "  calcular uma operação:   python3 calc.py --input operacao.json\n"
                "  arrumar vírgula e data:  cat operacao.json | python3 calc.py --humano\n"
                "  listar atividades:       python3 calc.py --atividades 2027-01-01 --anexo I\n"
                "  uma atividade:           python3 calc.py --atividade 01 --data 2027-01-01\n"
                "  explicar um erro:        python3 calc.py --erro VSN-001\n"
                "  versões da skill:        python3 calc.py --versao\n"
                "  há versão nova?          python3 calc.py --checar-versao"),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    modo = parser.add_mutually_exclusive_group()
    modo.add_argument("--input", help="arquivo JSON da operação (padrão: entrada padrão)")
    parser.add_argument("--humano", action="store_true",
                        help="aceita vírgula decimal, milhar, número sem aspas, NCM com pontos e data DD/MM/AAAA, "
                             "e lista o que mudou em `normalizacoes`")
    modo.add_argument("--atividades", metavar="DATA", help="lista as atividades vigentes na data (AAAA-MM-DD)")
    parser.add_argument("--anexo", action="append", help="filtra por anexo (I a V); pode repetir")
    parser.add_argument("--tp-doc", help="filtra pelo tipo de documento fiscal, como 55")
    parser.add_argument("--cclasstrib", help="filtra pela classificação tributária, como 000001")
    modo.add_argument("--atividade", metavar="CODIGO", help="mostra uma atividade pelo código (use com --data)")
    parser.add_argument("--data", help="data da consulta, AAAA-MM-DD (com --atividade; sem ela a resposta é o 400 da oficial)")
    modo.add_argument("--erro", metavar="VSN-001", help="explica um código de erro")
    modo.add_argument("--versao", action="store_true", help="mostra as versões da calculadora e da base")
    modo.add_argument("--checar-versao", action="store_true",
                        help="consulta o GitHub da Receita e diz se há versão nova (única ida à internet)")
    return parser


def _ler_operacao(args):
    """Devolve (operação, None) ou (None, mensagem de erro)."""
    try:
        if args.input:
            with open(args.input, encoding="utf-8-sig") as fh:
                raw = fh.read()
        else:
            raw = sys.stdin.read()
    except OSError as exc:
        return None, f"Não consegui ler o arquivo de entrada: {exc.strerror or exc}."
    except ValueError:
        return None, "A entrada não está em UTF-8."
    try:
        return json.loads(raw.lstrip("﻿"), parse_float=Decimal, parse_constant=_recusar_constante), None
    except ValueError as exc:
        return None, f"JSON inválido: {exc}."


def main(argv=None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    consulta = (args.atividades is not None or args.atividade is not None or args.erro is not None
                or args.versao or args.checar_versao)
    if args.humano and consulta:
        parser.error("--humano só vale para o cálculo de uma operação, não para as consultas")
    if args.data is not None and args.atividade is None:
        parser.error("--data só vale junto de --atividade (em --atividades a data vem logo depois)")
    for stream in (sys.stdin, sys.stdout):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        if args.checar_versao:
            try:
                with simples_base.Base() as base:
                    meta = base.meta()
            except Exception:  # esta consulta nunca derruba: sem meta, so informa a ultima versao
                meta = {}
            _imprimir(checar_versao(meta))
            return 0
        operacao = None
        if args.atividades is None and args.atividade is None and args.erro is None and not args.versao:
            operacao, erro = _ler_operacao(args)
            if erro:
                return _fail([erro])
        with simples_base.Base() as base:
            if args.atividades is not None:
                status, corpo = base.atividades(args.atividades, args.anexo, args.tp_doc, args.cclasstrib,
                                                base_url=BASE_URL_CLI)
                saida = _resposta(status, corpo, base)
            elif args.atividade is not None:
                status, corpo = base.atividade(args.atividade, args.data, args.tp_doc, args.cclasstrib,
                                               base_url=BASE_URL_CLI)
                saida = _resposta(status, corpo, base)
            elif args.erro is not None:
                status, corpo = simples_motor.descrever_erro(args.erro)
                saida = _resposta(status, corpo, base)
            elif args.versao:
                saida = _executar_versao(base)
            else:
                saida = executar_operacao(operacao, base, humano=args.humano)
        texto = json.dumps(saida, ensure_ascii=False, indent=2)
    except simples_base.BaseIndisponivel as exc:
        return _fail([f"A base da Receita não está disponível: {exc}"])
    except Exception as exc:  # rede de seguranca: o motor responde com status, nao levanta
        return _fail([f"Erro inesperado ao calcular: {type(exc).__name__}."])
    print(texto)
    return 0


if __name__ == "__main__":
    sys.exit(main())
