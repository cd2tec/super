#!/usr/bin/env python3
"""Sugestão de classificação tributária (cClassTrib) a partir do NCM, para a venda de mercadoria no regime
regular (CBS, IBS e Imposto Seletivo).

Esta é a leitura de varejo da CD2, não é código da Receita Federal e não entra na conferência com a
calculadora oficial. Resultado gerencial: a skill não é um aplicativo da Receita Federal, confira com o
contador.

Uso:
    python3 sugestao.py --ncm 1006.30.21 --tp-doc 65 --data 2027-03-15

O perfil abaixo vem do perfil de varejo do mini app de imposto por produto da CD2 (MIT), com a
referência legal que ele mostra para cada classificação. Uma sugestão só sai se a base da Receita
aceitar o NCM com aquela classificação, no tipo de documento e na data, pelas mesmas consultas que o
motor usa. No regime regular a redução sugerida vale de verdade: o motor a aplica na conta.

Só biblioteca padrão (Python 3.10+).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

_AQUI = Path(__file__).resolve().parent
if str(_AQUI) not in sys.path:
    sys.path.append(str(_AQUI))

import rr_base  # noqa: E402

ORIGEM = "leitura-cd2"
AVISO = "Classificação sugerida pela CD2, confirme com o contador."

# Tipo de classificação de cada entrada:
#   lista: a base traz a lista de NCM do anexo. Só vale se o NCM estiver nela.
#   dica:  a base não lista NCM (o benefício depende do produto). Sai só quando o NCM começa por um
#          dos prefixos de "dicas", e sempre com o pedido de conferir.
#   geral: tributação integral, a regra geral.
# "referencia" é o texto que o mini app mostra para a classificação (LC 214/2025 e o artigo).
# A redução de cada classificação e a incidência do Imposto Seletivo NÃO ficam aqui: vêm da base da Receita, na
# data pedida (Base.buscar_percentual_reducao e as alíquotas de IS). O perfil guarda só a leitura da CD2.
PERFIL = {
    "geral": "000001",
    "classificacoes": {
        "000001": {"tipo": "geral", "grupo": "geral",
                   "descricao": "Situações tributadas integralmente pelo IBS e CBS.",
                   "referencia": "LC 214/2025"},
        "200003": {"tipo": "lista", "grupo": "cesta",
                   "descricao": "Vendas de produtos destinados à alimentação humana (Anexo I)",
                   "referencia": "LC 214/2025, Art. 125"},
        "200014": {"tipo": "lista", "grupo": "hortifruti",
                   "descricao": "Fornecimento dos produtos hortícolas, frutas e ovos (Anexo XV)",
                   "referencia": "LC 214/2025, Art. 148"},
        "200034": {"tipo": "lista", "grupo": "alimento",
                   "descricao": "Fornecimento dos alimentos destinados ao consumo humano (Anexo VII)",
                   "referencia": "LC 214/2025, Art. 135"},
        "200035": {"tipo": "lista", "grupo": "higiene",
                   "descricao": "Fornecimento dos produtos de higiene pessoal e limpeza (Anexo VIII)",
                   "referencia": "LC 214/2025, Art. 136"},
        "200004": {"tipo": "lista", "grupo": "medico",
                   "descricao": "Fornecimento de dispositivos médicos (Anexo XII)",
                   "referencia": "LC 214/2025, Art. 144, I"},
        "200030": {"tipo": "lista", "grupo": "medico",
                   "descricao": "Venda dos dispositivos médicos (Anexo IV)",
                   "referencia": "LC 214/2025, Art. 131"},
        "200007": {"tipo": "lista", "grupo": "acessibilidade",
                   "descricao": "Fornecimento dos dispositivos de acessibilidade próprios para pessoas com "
                                "deficiência (Anexo XIII)",
                   "referencia": "LC 214/2025, Art. 145, I"},
        "200031": {"tipo": "lista", "grupo": "acessibilidade",
                   "descricao": "Fornecimento dos dispositivos de acessibilidade próprios para pessoas com "
                                "deficiência (Anexo V)",
                   "referencia": "LC 214/2025, Art. 132"},
        "200033": {"tipo": "lista", "grupo": "nutricao",
                   "descricao": "Fornecimento das composições para nutrição enteral e parenteral (Anexo VI)",
                   "referencia": "LC 214/2025, Art. 133, § 1º"},
        "200032": {"tipo": "dica", "grupo": "medicamento",
                   "descricao": "Fornecimento dos medicamentos registrados na Anvisa ou produzidos por "
                                "farmácias de manipulação, ressalvados os medicamentos sujeitos à "
                                "alíquota zero",
                   "referencia": "LC 214/2025, Art. 133"},
        "200009": {"tipo": "dica", "grupo": "medicamento",
                   "descricao": "Fornecimento dos medicamentos registrados na Anvisa",
                   "referencia": "LC 214/2025, Art. 146"},
        "200013": {"tipo": "dica", "grupo": "absorvente",
                   "descricao": "Fornecimento de tampões higiênicos, absorventes higiênicos internos ou "
                                "externos",
                   "referencia": "LC 214/2025, Art. 147"},
    },
    # Dicas: NCM que começa por um prefixo pode ter o benefício, que a tabela oficial não lista por NCM.
    "dicas": [
        {"id": "medicamento", "prefixos": ("3002", "3003", "3004"), "codigos": ("200032", "200009")},
        {"id": "absorvente", "prefixos": ("96190000",), "codigos": ("200013",)},
    ],
}

_SEPARADO = re.compile(r"\d+(?:[. \-]\d+)+")  # mesma regra do calc.normalizar para NCM
_ESCOPO_DOCUMENTOS = ("NFE", "NFCE")
TRIBUTO_CBS, TRIBUTO_IBS_ESTADUAL, TRIBUTO_IBS_MUNICIPAL = 2, 3, 4


# ------------------------------------------------------------- NCM

def _limpar(texto):
    """(NCM, avisos, preenchido). Só tira ponto, espaço e hífen entre grupos de dígitos (como o
    calc.normalizar) e completa o zero à esquerda de um código de 7 dígitos, dizendo que fez isso.
    Isto é só a sugestão: o calc.py não corrige o NCM, repassa como veio e o erro oficial aparece."""
    avisos = []
    if not isinstance(texto, str):
        return None, ["Informe o NCM como texto, com 8 dígitos."], False
    bruto = texto.strip()
    if not bruto:
        return None, ["O NCM veio vazio. Informe os 8 dígitos do NCM."], False
    limpo = re.sub(r"\D", "", bruto) if _SEPARADO.fullmatch(bruto) else bruto
    if limpo != bruto:
        avisos.append(f'Troquei "{bruto}" por "{limpo}" (só os dígitos). '
                      f"Na operação do calc.py, informe o NCM assim: {limpo}.")
    if not limpo.isascii() or not limpo.isdigit():
        avisos.append(f'O NCM "{bruto}" tem caracteres que não são dígitos. Use só números, com 8 dígitos.')
        return None, avisos, False
    if len(limpo) == 7:
        preenchido = "0" + limpo
        avisos.append(f"O NCM {limpo} tem 7 dígitos. Tentei com zero à esquerda: {preenchido}. "
                      "Confira se é esse mesmo. O calc.py não completa o zero: informe o NCM com os 8 dígitos.")
        return preenchido, avisos, True
    if len(limpo) != 8:
        avisos.append(f"O NCM {limpo} tem {len(limpo)} dígitos. O NCM completo tem 8.")
        return None, avisos, False
    return limpo, avisos, False


def normalizar_ncm(texto):
    """(NCM de 8 dígitos ou None, avisos). Não consulta a base: só o formato."""
    ncm, avisos, _ = _limpar(texto)
    return ncm, avisos


# ------------------------------------------------------------- aceite pela base

def _aceita(base, codigo, ncm, tipo_dfe, data, exige_lista):
    """A classificação (dict da base) quando ela aceita o NCM neste documento e nesta data, pelas consultas
    do motor (vínculo com o tipo de documento, nomenclatura e crítica contra o anexo); senão None."""
    try:
        classificacao = base.buscar_classificacao_tributaria(codigo, TRIBUTO_CBS, data)
        if classificacao is None:
            return None
        id_classificacao = classificacao["id"]
        if not any(v["tipoDfe"]["id"] == tipo_dfe["id"]
                   for v in base.buscar_tipo_dfe_classificacao(id_classificacao, data)):
            return None
        if base.determinar_nomenclatura(tipo_dfe["sigla"], codigo, data) in ("NBS", "EXCECAO_NBS"):
            return None
        tem_anexo = base.tem_ncm_aplicavel_para_classificacao(id_classificacao, data)
        # Classificação com lista de NCM: sem a lista a base aceitaria qualquer NCM, e isso não é sugestão.
        if exige_lista and not tem_anexo:
            return None
        if tem_anexo and (not base.tem_ncm_aplicavel(ncm, id_classificacao, data)
                          or (base.tem_excecao_ncm_aplicavel(ncm, id_classificacao, data)
                              and not base.tem_ncm_aplicavel_sem_excecao(ncm, id_classificacao, data))):
            return None
    except (rr_base.ValidacaoException, rr_base.EntidadeNaoEncontradaException, rr_base.ResultadoNaoUnico):
        return None
    return classificacao


def _texto(valor):
    """Percentual como a base o dá, sem zeros à direita: 100.0 vira "100", 30.50 vira "30.5"."""
    return format(valor.normalize(), "f")


def _reducoes(base, classificacao, data):
    """{"cbs", "ibsUf", "ibsMun"} em Decimal (0 sem linha vigente), pelas mesmas consultas do motor."""
    id_classificacao = classificacao["id"]
    resultado = {}
    for nome, tributo in (("cbs", TRIBUTO_CBS), ("ibsUf", TRIBUTO_IBS_ESTADUAL),
                          ("ibsMun", TRIBUTO_IBS_MUNICIPAL)):
        valor = base.buscar_percentual_reducao(id_classificacao, tributo, data)
        resultado[nome] = Decimal(0) if valor is None else valor
    return resultado


def _frase_reducao(r):
    if r["cbs"] == r["ibsUf"] == r["ibsMun"]:
        return f"Redução de {_texto(r['cbs'])}%"
    return (f"Redução de {_texto(r['cbs'])}% na CBS, {_texto(r['ibsUf'])}% no IBS estadual e "
            f"{_texto(r['ibsMun'])}% no IBS municipal")


def _texto_reducao(reducoes):
    """O que a classificação faz na conta do regime regular. A redução vale: o motor a aplica."""
    if all(v == 0 for v in reducoes.values()):
        return "Sem redução: a CBS e o IBS saem pela alíquota cheia."
    frase = _frase_reducao(reducoes)
    return f"{frase}, e a conta do regime regular aplica essa redução à CBS e ao IBS."


def _sugestao(codigo, classificacao, motivo, reducoes):
    perfil = PERFIL["classificacoes"][codigo]
    saida = {"cClassTrib": codigo, "cst": classificacao["cst"], "descricao": perfil["descricao"],
             "reducao": _texto(reducoes["cbs"]), "motivo": motivo}
    if not reducoes["cbs"] == reducoes["ibsUf"] == reducoes["ibsMun"]:
        # `reducao` fica com a da CBS; aqui vão as três quando divergem
        saida["reducoes"] = {"cbs": _texto(reducoes["cbs"]), "ibsEstadual": _texto(reducoes["ibsUf"]),
                             "ibsMunicipal": _texto(reducoes["ibsMun"])}
    return saida


def _imposto_seletivo(base, ncm, data):
    """A incidência é a do motor (ProcessamentoItemService.existeIncidenciaImpostoSeletivo): alíquota ad
    valorem ou ad rem do IS vigente para o NCM, já descontadas as exceções da tabela."""
    versao = base.versao_base()["versao"]
    if (base.existe_ncm_ad_valorem(ncm, rr_base.TRIBUTO_IS, data)
            or base.buscar_aliquota_ad_rem(ncm, rr_base.TRIBUTO_IS, data) is not None):
        texto = (f"Este NCM tem Imposto Seletivo na base da Receita ({versao}), que começa em 2027. "
                 "A alíquota ainda não foi definida em lei: de 2027 em diante informe-a em "
                 "aliquotas.imposto_seletivo (ad_valorem ou ad_rem) e traga o bloco imposto_seletivo do item, "
                 "com a classificação e o CST do Imposto Seletivo. Sem eles a calculadora responde com o erro "
                 "oficial. Esta sugestão não escolhe a classificação do Imposto Seletivo. "
                 "Confirme com o contador.")
        return {"na_lista": True, "texto": texto}
    return {"na_lista": False,
            "texto": "Este NCM não tem Imposto Seletivo na base da Receita nesta data."}


LEITURA = ("A primeira sugestão é a mais provável para um produto deste NCM. A última, 000001, é a regra "
           "geral, para o produto que não for o item do anexo. No regime regular, a redução de cada "
           "classificação vale na conta: a escolha muda o valor da CBS e do IBS.")
LEITURA_VAZIA = "Nenhuma classificação foi sugerida para este NCM."


def _tipo_dfe(base, tp_doc, data, avisos):
    """O tipo de documento (dict da base) quando a sugestão vale para ele; senão None, com o aviso."""
    tipo = base.buscar_por_tipo(tp_doc, data) if isinstance(tp_doc, int) and not isinstance(tp_doc, bool) else None
    if tipo is None:
        avisos.append(f"O tipo de documento {tp_doc} não foi encontrado ou está fora de vigência em "
                      f"{data.isoformat()}. A sugestão vale para NF-e (55) e NFC-e (65).")
        return None
    if rr_base.normalizar_sigla(tipo["sigla"]) not in _ESCOPO_DOCUMENTOS:
        avisos.append(f"O tipo de documento {tp_doc} não é NF-e (55) nem NFC-e (65). "
                      "A sugestão vale só para venda de mercadoria.")
        return None
    return tipo


def sugerir(ncm, tp_doc, data, base):
    """Sugere classificações para o NCM. Veja o módulo: é leitura da CD2, não da Receita."""
    ncm_ok, avisos, preenchido = _limpar(ncm)
    resultado = {"ncm": ncm_ok, "avisos": avisos, "leitura": LEITURA_VAZIA, "sugestoes": [],
                 "origem": ORIGEM}
    if ncm_ok is None:
        return resultado
    tipo_dfe = _tipo_dfe(base, tp_doc, data, avisos)
    if tipo_dfe is None:
        return resultado
    if not base.existe_ncm(ncm_ok, data):
        if preenchido:
            avisos.append(f"O NCM {ncm_ok[1:]} com zero à esquerda ({ncm_ok}) também não existe na base. "
                          "NCM não encontrado.")
        else:
            avisos.append(f"NCM {ncm_ok} não encontrado na base da Receita nesta data. Confira o código.")
        resultado["ncm"] = None
        return resultado

    resultado["imposto_seletivo"] = _imposto_seletivo(base, ncm_ok, data)
    classes = PERFIL["classificacoes"]
    # Menor redução primeiro (a da CBS), desempate pelo código (a mesma ordem do mini app, classify.ts).
    achadas = []
    for codigo, perfil in classes.items():
        if perfil["tipo"] != "lista":
            continue
        classificacao = _aceita(base, codigo, ncm_ok, tipo_dfe, data, exige_lista=True)
        if classificacao is not None:
            achadas.append((_reducoes(base, classificacao, data), codigo, classificacao))
    achadas.sort(key=lambda a: (a[0]["cbs"], a[1]))
    sugestoes, listas = [], []
    for reducoes, codigo, classificacao in achadas:
        perfil = classes[codigo]
        listas.append(codigo)
        sugestoes.append(_sugestao(
            codigo, classificacao,
            f"O NCM {ncm_ok} está na lista desta classificação: {perfil['descricao']}. "
            f"{_texto_reducao(reducoes)} Base legal: {perfil['referencia']}. "
            "A lista é por NCM: confira se o seu produto é o item descrito no anexo.", reducoes))
    if len(listas) > 1:
        primeira = _texto(achadas[0][0]["cbs"])
        avisos.append(f"Este NCM cabe em mais de uma lista ({', '.join(listas)}). A primeira sugestão é a de "
                      f"menor redução ({primeira}%). Como a redução vale na conta, a escolha muda o valor da "
                      "CBS e do IBS. Só a natureza do seu produto decide qual vale.")

    dica = next((d for d in PERFIL["dicas"] if any(ncm_ok.startswith(p) for p in d["prefixos"])), None)
    if dica:
        # Como no mini app, a dica nunca passa à frente de uma lista que o NCM de fato cumpre: fica depois
        # dela, e só avisa quando tem mais redução do que a lista.
        for codigo in dica["codigos"]:
            classificacao = _aceita(base, codigo, ncm_ok, tipo_dfe, data, exige_lista=False)
            if classificacao is None:
                continue
            perfil = classes[codigo]
            reducoes = _reducoes(base, classificacao, data)
            sugestoes.append(_sugestao(
                codigo, classificacao,
                f"Produtos deste NCM podem ter esta classificação: {perfil['descricao']}. "
                f"{_texto_reducao(reducoes)} Base legal: {perfil['referencia']}. A tabela da Receita não "
                "lista este benefício por NCM. Só use se o produto se enquadrar, e confirme com o "
                "contador.", reducoes))
            if achadas and reducoes["cbs"] > achadas[0][0]["cbs"]:
                avisos.append(f"Se o produto for do tipo {dica['id']}, a {codigo} tem mais redução "
                              f"({_texto(reducoes['cbs'])}%) do que a lista em que o NCM está, e ela vale na "
                              "conta. Confira o produto.")
    if not sugestoes:
        avisos.append(f"Nenhuma classificação com benefício da leitura de varejo da CD2 cabe no NCM {ncm_ok}.")

    geral = PERFIL["geral"]
    classificacao = _aceita(base, geral, ncm_ok, tipo_dfe, data, exige_lista=False)
    if classificacao is not None:
        perfil = classes[geral]
        reducoes = _reducoes(base, classificacao, data)
        regra = "Regra geral, sem redução" if all(v == 0 for v in reducoes.values()) else "Regra geral"
        if sugestoes:
            motivo = (f"{regra}. {_texto_reducao(reducoes)} Vale para o produto que não for o item do "
                      f"anexo. Base legal: {perfil['referencia']}.")
        else:
            motivo = (f"{regra}: o NCM {ncm_ok} não está em nenhuma lista do perfil de varejo da CD2. "
                      f"{_texto_reducao(reducoes)} Base legal: {perfil['referencia']}.")
        sugestoes.append(_sugestao(geral, classificacao, motivo, reducoes))
    elif not sugestoes:
        avisos.append("A base da Receita não aceita nem a tributação integral para este NCM, "
                      "neste documento e nesta data. Não vou chutar: confirme com o contador.")
    resultado["sugestoes"] = sugestoes
    if sugestoes:
        resultado["leitura"] = LEITURA
    return resultado


# ------------------------------------------------------------- linha de comando

def _data(texto):
    try:
        return date.fromisoformat(texto)
    except ValueError:
        raise argparse.ArgumentTypeError(f"data inválida: {texto!r}. Use AAAA-MM-DD.")


def principal(argv=None):
    parser = argparse.ArgumentParser(
        description="Sugere o cClassTrib de um NCM para a venda no caixa (regime regular). "
                    "Leitura da CD2, não da Receita.")
    parser.add_argument("--ncm", required=True, help="NCM, com ou sem pontos (8 dígitos)")
    parser.add_argument("--tp-doc", type=int, default=65, help="55 (NF-e) ou 65 (NFC-e). Padrão: 65")
    parser.add_argument("--data", type=_data, required=True, help="AAAA-MM-DD")
    args = parser.parse_args(argv)
    try:
        with rr_base.Base() as base:
            saida = sugerir(args.ncm, args.tp_doc, args.data, base)
    except rr_base.BaseIndisponivel as erro:
        print(str(erro), file=sys.stderr)
        return 2
    saida["aviso"] = AVISO
    print(json.dumps(saida, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(principal())
