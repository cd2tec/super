#!/usr/bin/env python3
"""Sugestão de classificação tributária (cClassTrib) a partir do NCM, para a venda comum no caixa.

Esta é a leitura de varejo da CD2, não é código da Receita Federal e não entra na conferência com a
calculadora oficial. Resultado gerencial: a skill não é um aplicativo da Receita Federal, confira com o
contador.

Uso:
    python3 sugestao.py --ncm 1006.30.21 --tp-doc 65 --data 2027-03-15

O perfil abaixo vem do perfil de varejo do mini app de imposto por produto da CD2 (MIT), com a
referência legal que ele mostra para cada classificação. Uma sugestão só sai se a base da Receita
aceitar o NCM com aquela classificação, no tipo de documento e na data, pela mesma validação que o
motor usa.

So biblioteca padrao (Python 3.10+).
"""
import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

_AQUI = Path(__file__).resolve().parent
if str(_AQUI) not in sys.path:
    sys.path.append(str(_AQUI))

import simples_base  # noqa: E402

ORIGEM = "leitura-cd2"
AVISO = "Classificação sugerida pela CD2, confirme com o contador."

# Tipo de classificação de cada entrada:
#   lista: a base traz a lista de NCM do anexo. Só vale se o NCM estiver nela.
#   dica:  a base não lista NCM (o benefício depende do produto). Sai só quando o NCM começa por um
#          dos prefixos de "dicas", e sempre com o pedido de conferir.
#   geral: tributação integral, a regra geral.
# "referencia" é o texto que o mini app mostra para a classificação (LC 214/2025 e o artigo).
# "reducao" é a redução de CBS e IBS em pontos percentuais. A base reduzida não traz esse dado: vem do mini app
# (base V0073) e o teste test_reducao_do_perfil_nao_muda_sozinha trava o valor. Ordena as listas.
PERFIL = {
    "geral": "000001",
    "classificacoes": {
        "000001": {"tipo": "geral", "grupo": "geral", "reducao": 0,
                   "descricao": "Situações tributadas integralmente pelo IBS e CBS.",
                   "referencia": "LC 214/2025"},
        "200003": {"tipo": "lista", "grupo": "cesta", "reducao": 100,
                   "descricao": "Vendas de produtos destinados à alimentação humana (Anexo I)",
                   "referencia": "LC 214/2025, Art. 125"},
        "200014": {"tipo": "lista", "grupo": "hortifruti", "reducao": 100,
                   "descricao": "Fornecimento dos produtos hortícolas, frutas e ovos (Anexo XV)",
                   "referencia": "LC 214/2025, Art. 148"},
        "200034": {"tipo": "lista", "grupo": "alimento", "reducao": 60,
                   "descricao": "Fornecimento dos alimentos destinados ao consumo humano (Anexo VII)",
                   "referencia": "LC 214/2025, Art. 135"},
        "200035": {"tipo": "lista", "grupo": "higiene", "reducao": 60,
                   "descricao": "Fornecimento dos produtos de higiene pessoal e limpeza (Anexo VIII)",
                   "referencia": "LC 214/2025, Art. 136"},
        "200004": {"tipo": "lista", "grupo": "medico", "reducao": 100,
                   "descricao": "Fornecimento de dispositivos médicos (Anexo XII)",
                   "referencia": "LC 214/2025, Art. 144, I"},
        "200030": {"tipo": "lista", "grupo": "medico", "reducao": 60,
                   "descricao": "Venda dos dispositivos médicos (Anexo IV)",
                   "referencia": "LC 214/2025, Art. 131"},
        "200007": {"tipo": "lista", "grupo": "acessibilidade", "reducao": 100,
                   "descricao": "Fornecimento dos dispositivos de acessibilidade próprios para pessoas com "
                                "deficiência (Anexo XIII)",
                   "referencia": "LC 214/2025, Art. 145, I"},
        "200031": {"tipo": "lista", "grupo": "acessibilidade", "reducao": 60,
                   "descricao": "Fornecimento dos dispositivos de acessibilidade próprios para pessoas com "
                                "deficiência (Anexo V)",
                   "referencia": "LC 214/2025, Art. 132"},
        "200033": {"tipo": "lista", "grupo": "nutricao", "reducao": 60,
                   "descricao": "Fornecimento das composições para nutrição enteral e parenteral (Anexo VI)",
                   "referencia": "LC 214/2025, Art. 133, § 1º"},
        "200032": {"tipo": "dica", "grupo": "medicamento", "reducao": 60,
                   "descricao": "Fornecimento dos medicamentos registrados na Anvisa ou produzidos por "
                                "farmácias de manipulação, ressalvados os medicamentos sujeitos à "
                                "alíquota zero",
                   "referencia": "LC 214/2025, Art. 133"},
        "200009": {"tipo": "dica", "grupo": "medicamento", "reducao": 100,
                   "descricao": "Fornecimento dos medicamentos registrados na Anvisa",
                   "referencia": "LC 214/2025, Art. 146"},
        "200013": {"tipo": "dica", "grupo": "absorvente", "reducao": 100,
                   "descricao": "Fornecimento de tampões higiênicos, absorventes higiênicos internos ou "
                                "externos",
                   "referencia": "LC 214/2025, Art. 147"},
    },
    # Imposto Seletivo, do mini app da CD2 (que copia a consulta de NCM dos dados abertos da calculadora do
    # regime regular da Receita: commit f1eebd7, base V0073). Um NCM está na lista quando começa por um dos
    # prefixos e nenhuma exceção o cobre. Ele começa em 2027, e a alíquota ainda não está definida em lei.
    "imposto_seletivo": {
        "fonte": "dados abertos da Receita Federal, base V0073",
        "excecoes": {"88021100": ("88026000",)},
        "prefixos": (
            "22021000", "22030000", "22041010", "22041090", "22042211", "22042219", "22042220", "22042910",
            "22042920", "22043000", "22051000", "22059000", "22060010", "22060090", "22083010", "22083020",
            "22083090", "22084000", "22085000", "22086000", "22087000", "22089000", "24011010", "24011020",
            "24011030", "24011040", "24011090", "24012010", "24012020", "24012030", "24012040", "24012090",
            "24013000", "24021000", "24022000", "24029000", "24031100", "24031900", "24039100", "24039910",
            "24039990", "24041100", "24041200", "24041900", "24049100", "24049200", "24049900", "26011100",
            "26011210", "26011290", "26012000", "27090010", "27111100", "27112100", "87032100", "87032210",
            "87032290", "87032310", "87032390", "87032410", "87032490", "87033110", "87033190", "87033210",
            "87033290", "87033310", "87033390", "87034000", "87035000", "87036000", "87037000", "87039000",
            "8802", "88021100", "88021210", "88021290", "88022010", "88022021", "88022022", "88022090",
            "88023010", "88023021", "88023029", "88023031", "88023039", "88023090", "88024010", "88024090",
            "88026000", "8903", "89031100", "89031200", "89031900", "89032100", "89032200", "89032300",
            "89033100", "89033200", "89033300", "89039300", "89039900",
        ),
    },
    # Dicas: NCM que começa por um prefixo pode ter o benefício, que a tabela oficial não lista por NCM.
    "dicas": [
        {"id": "medicamento", "prefixos": ("3002", "3003", "3004"), "codigos": ("200032", "200009")},
        {"id": "absorvente", "prefixos": ("96190000",), "codigos": ("200013",)},
    ],
}

_SEPARADO = re.compile(r"\d+(?:[. \-]\d+)+")  # mesma regra do calc.normalizar para NCM
_ESCOPO_DOCUMENTOS = ("NFE", "NFCE")


# ------------------------------------------------------------- NCM

def _limpar(texto):
    """(NCM, avisos, preenchido). Só tira ponto, espaço e hífen entre grupos de dígitos (como o
    calc.normalizar) e completa o zero à esquerda de um código de 7 dígitos, dizendo que fez isso."""
    avisos = []
    if not isinstance(texto, str):
        return None, ["Informe o NCM como texto, com 8 dígitos."], False
    bruto = texto.strip()
    if not bruto:
        return None, ["O NCM veio vazio. Informe os 8 dígitos do NCM."], False
    limpo = re.sub(r"\D", "", bruto) if _SEPARADO.fullmatch(bruto) else bruto
    if limpo != bruto:
        avisos.append(f'Troquei "{bruto}" por "{limpo}" (só os dígitos).')
    if not limpo.isascii() or not limpo.isdigit():
        avisos.append(f'O NCM "{bruto}" tem caracteres que não são dígitos. Use só números, com 8 dígitos.')
        return None, avisos, False
    if len(limpo) == 7:
        preenchido = "0" + limpo
        avisos.append(f"O NCM {limpo} tem 7 dígitos. Tentei com zero à esquerda: {preenchido}. "
                      "Confira se é esse mesmo.")
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

def _aceita(base, codigo, ncm, sigla, data, exige_lista):
    """True quando a base aceita o NCM com a classificação, no documento e na data, pela validação do motor."""
    try:
        classificacao = base.classificacao_cbs_ibs(codigo, data)
    except (simples_base.ValidacaoException, simples_base.EntidadeNaoEncontradaException,
            simples_base.ResultadoNaoUnico):
        return None
    siglas = {simples_base.normalizar_sigla(t["tipoDfe"]["sigla"])
              for t in base.tipo_dfe_classificacao(classificacao["id"], data)}
    if simples_base.normalizar_sigla(sigla) not in siglas:
        return None
    # Classificação com lista de NCM: sem a lista a base aceitaria qualquer NCM, e isso não é sugestão.
    if exige_lista and not base.tem_ncm_aplicavel_para_classificacao(classificacao["id"], data):
        return None
    try:
        base.validar_nomenclatura(sigla, classificacao["id"], codigo, ncm, None, data, "CBS e IBS",
                                  simples_base.MODO_PERMISSIVO)
    except simples_base.ValidacaoException:
        return None
    return classificacao


def _sugestao(codigo, classificacao, motivo):
    perfil = PERFIL["classificacoes"][codigo]
    return {"cClassTrib": codigo, "cst": classificacao["cst"], "descricao": perfil["descricao"],
            "reducao": str(perfil["reducao"]), "motivo": motivo}


def _no_imposto_seletivo(ncm):
    """Mesma regra do mini app (isSelective): prefixo da lista, fora as exceções."""
    lista = PERFIL["imposto_seletivo"]
    for prefixo in lista["prefixos"]:
        if ncm.startswith(prefixo):
            excecoes = lista["excecoes"].get(prefixo, ())
            if not any(ncm.startswith(e) or prefixo.startswith(e) for e in excecoes):
                return True
    return False


def _imposto_seletivo(ncm):
    fonte = PERFIL["imposto_seletivo"]["fonte"]
    if _no_imposto_seletivo(ncm):
        texto = (f"Este NCM está na leitura da CD2 da lista do Imposto Seletivo ({fonte}), que começa em 2027. "
                 "Esta ferramenta não calcula o Imposto Seletivo, e a alíquota ainda não foi definida em lei. "
                 "O documento precisa do valor em valorImpostoSeletivo. Confirme com o contador.")
        return {"na_lista": True, "texto": texto}
    return {"na_lista": False,
            "texto": "Este NCM não está na leitura da CD2 da lista do Imposto Seletivo. "
                     "Esta ferramenta não verifica o Imposto Seletivo além dessa lista."}


LEITURA = ("A primeira sugestão é a mais provável para um produto deste NCM. A última, 000001, é a regra "
           "geral, para o produto que não for o item do anexo.")
LEITURA_VAZIA = "Nenhuma classificação foi sugerida para este NCM."


def sugerir(ncm, tp_doc, data, base):
    """Sugere classificações para o NCM. Veja o módulo: é leitura da CD2, não da Receita."""
    ncm_ok, avisos, preenchido = _limpar(ncm)
    resultado = {"ncm": ncm_ok, "avisos": avisos, "leitura": LEITURA_VAZIA, "sugestoes": [],
                 "origem": ORIGEM}
    if ncm_ok is None:
        return resultado
    documento = base.tipo_dfe_calculo(tp_doc, data) if isinstance(tp_doc, int) else None
    sigla = base.sigla_tipo_dfe_vigente(documento["tipoDfeId"], data) if documento else None
    if sigla is None:
        avisos.append(f"O tipo de documento {tp_doc} não é suportado em {data.isoformat()}. "
                      "A calculadora do Simples Nacional vale a partir de 2027-01-01, para NF-e (55) "
                      "e NFC-e (65).")
        return resultado
    if simples_base.normalizar_sigla(sigla) not in _ESCOPO_DOCUMENTOS:
        avisos.append(f"O tipo de documento {tp_doc} não é NF-e (55) nem NFC-e (65). "
                      "A sugestão vale só para venda de mercadoria.")
        return resultado
    if not base.existe_ncm(ncm_ok, data):
        if preenchido:
            avisos.append(f"O NCM {ncm_ok[1:]} com zero à esquerda ({ncm_ok}) também não existe na base. "
                          "NCM não encontrado.")
        else:
            avisos.append(f"NCM {ncm_ok} não encontrado na base da Receita nesta data. Confira o código.")
        resultado["ncm"] = None
        return resultado

    resultado["imposto_seletivo"] = _imposto_seletivo(ncm_ok)
    classes = PERFIL["classificacoes"]
    # Menor redução primeiro, desempate pelo código: o imposto mostrado nunca fica abaixo do real
    # (a mesma ordem do mini app, classify.ts).
    candidatas = sorted((c for c, p in classes.items() if p["tipo"] == "lista"),
                        key=lambda c: (classes[c]["reducao"], c))
    sugestoes, listas = [], []
    for codigo in candidatas:
        perfil = classes[codigo]
        classificacao = _aceita(base, codigo, ncm_ok, sigla, data, exige_lista=True)
        if classificacao is None:
            continue
        listas.append(codigo)
        sugestoes.append(_sugestao(
            codigo, classificacao,
            f"O NCM {ncm_ok} está na lista desta classificação: {perfil['descricao']}. "
            f"Redução de {perfil['reducao']}%. Base legal: {perfil['referencia']}. "
            "A lista é por NCM: confira se o seu produto é o item descrito no anexo."))
    if len(listas) > 1:
        primeira = classes[listas[0]]["reducao"]
        avisos.append(f"Este NCM cabe em mais de uma lista ({', '.join(listas)}). A primeira sugestão é a de "
                      f"menor redução ({primeira}%): o imposto mostrado nunca fica abaixo do real. "
                      "Só a natureza do seu produto decide qual vale.")

    dica = next((d for d in PERFIL["dicas"] if any(ncm_ok.startswith(p) for p in d["prefixos"])), None)
    if dica:
        # Como no mini app, a dica nunca passa à frente de uma lista que o NCM de fato cumpre: fica depois
        # dela, e só avisa quando promete mais redução do que a lista.
        for codigo in dica["codigos"]:
            classificacao = _aceita(base, codigo, ncm_ok, sigla, data, exige_lista=False)
            if classificacao is None:
                continue
            perfil = classes[codigo]
            sugestoes.append(_sugestao(
                codigo, classificacao,
                f"Produtos deste NCM podem ter redução de {perfil['reducao']}%: {perfil['descricao']}. "
                f"Base legal: {perfil['referencia']}. A tabela da Receita não lista este benefício "
                "por NCM. Só use se o produto se enquadrar, e confirme com o contador."))
            if listas and perfil["reducao"] > classes[listas[0]]["reducao"]:
                avisos.append(f"Se o produto for do tipo {dica['id']}, a {codigo} promete mais redução "
                              f"({perfil['reducao']}%) do que a lista em que o NCM está. Confira o produto.")
    if not sugestoes:
        avisos.append(f"Nenhuma classificação com benefício da leitura de varejo da CD2 cabe no NCM {ncm_ok}.")

    geral = PERFIL["geral"]
    classificacao = _aceita(base, geral, ncm_ok, sigla, data, exige_lista=False)
    if classificacao is not None:
        perfil = classes[geral]
        if sugestoes:
            motivo = ("Regra geral, sem redução. Vale para o produto que não for o item do anexo. "
                      f"Base legal: {perfil['referencia']}.")
        else:
            motivo = (f"Regra geral, sem redução: o NCM {ncm_ok} não está em nenhuma lista do perfil de "
                      f"varejo da CD2. Base legal: {perfil['referencia']}.")
        sugestoes.append(_sugestao(geral, classificacao, motivo))
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
        description="Sugere o cClassTrib de um NCM para a venda no caixa. Leitura da CD2, não da Receita.")
    parser.add_argument("--ncm", required=True, help="NCM, com ou sem pontos (8 dígitos)")
    parser.add_argument("--tp-doc", type=int, default=65, help="55 (NF-e) ou 65 (NFC-e). Padrão: 65")
    parser.add_argument("--data", type=_data, required=True, help="AAAA-MM-DD (a partir de 2027-01-01)")
    args = parser.parse_args(argv)
    try:
        with simples_base.Base() as base:
            saida = sugerir(args.ncm, args.tp_doc, args.data, base)
    except simples_base.BaseIndisponivel as erro:
        print(str(erro), file=sys.stderr)
        return 2
    saida["aviso"] = AVISO
    print(json.dumps(saida, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(principal())
