# Port, feito pela CD2 Tech, do código de ReceitaFederal/calculadora-rtc-regime-regular,
# distribuído sob a licença MIT. Aviso de copyright e licença do original, como estão no LICENSE dele:
#
# MIT License
#
# Copyright (c) 2026 Receita Federal do Brasil
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
"""Memória de cálculo do item na skill cbs-ibs-regime-regular.

Port (calculadora-rtc-regime-regular, tag 1.7.2) de:
- domain/service/MemoriaCalculoService.java: `memoria_cbs_ibs` (gerarMemoriaCalculoCbsIbs) e `memoria_is`
  (gerarMemoriaCalculoImpostoSeletivo), com formatarMoeda e formatarPercentual (`formatar_moeda` e
  `formatar_percentual`: DecimalFormat "#,##0.00" e "#,##0.00##", símbolos de pt-BR, HALF_EVEN);
- domain/service/token/TemplateTokenizerService.java e TokenizerService.java: `tokenizar` e
  `substituir_placeholders`;
- java.math.BigDecimal.toString(): `texto_big_decimal`, para a [quantidade] (notação científica quando a
  escala é negativa ou o expoente ajustado é menor que -6).

A memória mostra os números publicados (já arredondados no cálculo); a formatação não muda o número.
O texto vem da classificação tributária (CLTR_MEMORIA_CALCULO) vigente na data, e os placeholders entre
colchetes são trocados pelos valores; placeholder sem valor vira texto vazio. application.memoriacalculo.
enabled é verdadeiro no perfil offline, então a memória sai sempre.
"""
from __future__ import annotations

import sys
import unicodedata
from decimal import ROUND_HALF_EVEN, Context, Decimal
from pathlib import Path

_AQUI = Path(__file__).resolve().parent
if str(_AQUI) not in sys.path:
    sys.path.append(str(_AQUI))

import rr_base  # noqa: E402  (precisa do caminho acima)
from rr_erros import ErroRR  # noqa: E402

ZERO = Decimal(0)
SEM_TEXTO = "Texto de memória de cálculo não encontrado"
_CONTEXTO = Context(prec=10000, rounding=ROUND_HALF_EVEN)

# Character.isWhitespace (o StringUtils.isBlank do commons-lang): os separadores Unicode, menos os sem quebra
_SEM_QUEBRA = frozenset("   ")
_CONTROLES = frozenset("\t\n\u000b\f\r\u001c\u001d\u001e\u001f")


def _espaco_java(c: str) -> bool:
    if c in _CONTROLES:
        return True
    return unicodedata.category(c) in ("Zs", "Zl", "Zp") and c not in _SEM_QUEBRA


def _em_branco(texto) -> bool:
    """StringUtils.isBlank."""
    return texto is None or all(_espaco_java(c) for c in texto)


# --- números ---------------------------------------------------------------------------------------

def texto_big_decimal(valor: Decimal) -> str:
    """BigDecimal.toString(): forma plana com escala >= 0 e expoente ajustado >= -6; senão, científica."""
    sinal, digitos, expoente = valor.as_tuple()
    coeficiente = "".join(map(str, digitos)).lstrip("0") or "0"
    negativo = sinal == 1 and coeficiente != "0"  # BigDecimal não tem zero negativo
    escala = -expoente
    ajustado = -escala + (len(coeficiente) - 1)
    if escala == 0:
        texto = coeficiente
    elif escala > 0 and ajustado >= -6:
        if len(coeficiente) > escala:
            texto = coeficiente[:-escala] + "." + coeficiente[-escala:]
        else:
            texto = "0." + "0" * (escala - len(coeficiente)) + coeficiente
    else:
        texto = coeficiente[0] + ("." + coeficiente[1:] if len(coeficiente) > 1 else "")
        if ajustado != 0:
            texto += "E" + ("+" if ajustado > 0 else "") + str(ajustado)
    return ("-" if negativo else "") + texto


def _decimal_format(valor, minimo_casas: int, maximo_casas: int) -> str:
    """DecimalFormat("#,##0.<mínimo><opcionais>") de um BigDecimal, com '.' no milhar e ',' nos decimais.

    Como no Java, o sinal vem do número antes do arredondamento: -0.001 sai "-0,00"."""
    valor = ZERO if valor is None else valor
    negativo = valor < 0
    arredondado = _CONTEXTO.quantize(abs(valor), Decimal(1).scaleb(-maximo_casas))
    inteiro, _, fracao = format(arredondado, "f").partition(".")
    fracao = fracao.ljust(maximo_casas, "0")
    while len(fracao) > minimo_casas and fracao.endswith("0"):
        fracao = fracao[:-1]
    inteiro = inteiro.lstrip("0") or "0"
    grupos = []
    while len(inteiro) > 3:
        grupos.insert(0, inteiro[-3:])
        inteiro = inteiro[:-3]
    grupos.insert(0, inteiro)
    texto = ".".join(grupos) + ("," + fracao if fracao else "")
    return ("-" if negativo else "") + texto


def formatar_moeda(valor) -> str:
    """MemoriaCalculoService.formatarMoeda: "#,##0.00"; nulo vale zero."""
    return _decimal_format(valor, 2, 2)


def formatar_percentual(valor) -> str:
    """MemoriaCalculoService.formatarPercentual: "#,##0.00##"; nulo vale zero."""
    return _decimal_format(valor, 2, 4)


# --- o tokenizer -----------------------------------------------------------------------------------

def tokenizar(modelo: str) -> list:
    """TemplateTokenizerService.tokenize: [(texto, None) | (None, chave)]. Colchete sem fechamento fica
    no texto; a chave vai do '[' ao primeiro ']' depois dele."""
    tokens = []
    ultimo, tamanho = 0, len(modelo)
    while ultimo < tamanho:
        abre = modelo.find("[", ultimo)
        if abre < 0:
            tokens.append((modelo[ultimo:], None))
            break
        fecha = modelo.find("]", abre + 1)
        if fecha < 0:
            tokens.append((modelo[ultimo:], None))
            break
        if abre > ultimo:
            tokens.append((modelo[ultimo:abre], None))
        tokens.append((None, modelo[abre + 1:fecha]))
        ultimo = fecha + 1
    return tokens


def substituir_placeholders(modelo: str, valores: dict) -> str:
    """TokenizerService.substituirPlaceholders: sem '[' o modelo volta igual; chave sem valor vira ""."""
    if "[" not in modelo:
        return modelo
    return "".join(texto if chave is None else valores.get(chave, "") for texto, chave in tokenizar(modelo))


# --- MemoriaCalculoService --------------------------------------------------------------------------

def _texto(valor) -> str:
    """StringUtils.defaultString."""
    return "" if valor is None else valor


def _fundamentacao(base, id_classificacao, data) -> dict:
    """FundamentacaoClassificacaoService.buscar."""
    try:
        achada = base.buscar_fundamentacao_classificacao(id_classificacao, data)
    except rr_base.ResultadoNaoUnico as erro:  # IncorrectResultSizeDataAccessException: sem código
        raise ErroRR(None, str(erro)) from erro
    if achada is None:
        raise ErroRR("CAL-011", f"Fundamentação legal não encontrada para classificação tributária de id "
                                f"{id_classificacao} e data {data.isoformat()}")
    return achada


def _norma_e_tratamento(base, tratamento_classificacao, data):
    """A fundamentação (antes) e o tratamento tributário (depois), na ordem do Java."""
    import rr_calculo  # aqui: rr_calculo importa este módulo
    fundamentacao = _fundamentacao(base, tratamento_classificacao["idClassificacaoTributaria"], data)
    tratamento = rr_calculo._buscar_tratamento_tributario(base, tratamento_classificacao["idTratamentoTributario"])
    return fundamentacao, tratamento


def _gerar(fundamentacao, valores) -> str:
    modelo = fundamentacao["memoriaCalculo"]
    return SEM_TEXTO if _em_branco(modelo) else substituir_placeholders(modelo, valores)


def memoria_cbs_ibs(tratamento_classificacao: dict, tributo: dict | None, base_calculo_informada,
                    quantidade, unidade, data, base) -> str:
    """gerarMemoriaCalculoCbsIbs: a memória de um tributo (CBS, IBS da UF ou do município).

    `tributo` é o CbsIbsOutput de rr_calculo.calcular_cbs_ibs; nulo (CST sem gIBSCBS, como a imunidade 410)
    vira um output só com a base informada no item. `quantidade` e `unidade` são as do item.
    """
    if tributo is None:
        tributo = {"baseCalculo": base_calculo_informada}
    fundamentacao, tratamento = _norma_e_tratamento(base, tratamento_classificacao, data)
    monofasia = tributo.get("grupoMonofasia")
    monofasico = None if monofasia is None else monofasia.get("tributoMonofasico")
    ad_rem = None if monofasico is None else monofasico.get("aliquotaAdRem")
    reducao = tributo.get("grupoReducao")
    diferimento = tributo.get("grupoDiferimento")
    p_dif = v_dif = None
    if diferimento is not None and diferimento.get("pDif") is not None and diferimento.get("vDif") is not None:
        p_dif, v_dif = diferimento["pDif"], diferimento["vDif"]
    valores = {
        "norma": _texto(fundamentacao["textoCurto"]),
        "tratamento": _texto(tratamento["descricao"]),
        "base_calculo": formatar_moeda(tributo.get("baseCalculo")),
        "aliquota_ad_valorem": formatar_percentual(tributo.get("aliquota")),
        "aliquota_ad_rem": formatar_percentual(ad_rem),
        "quantidade": texto_big_decimal(quantidade) if quantidade is not None else "0",
        "unidade": _texto(unidade),
        "percentual_reducao": formatar_percentual(None if reducao is None else reducao.get("pRedAliq")),
        "percentual_diferimento": formatar_percentual(p_dif),
        "valor_diferimento": formatar_moeda(v_dif),
        "aliquota_desoneracao": formatar_percentual(None),
        "montante_desoneracao": formatar_moeda(None),
    }
    return _gerar(fundamentacao, valores)


def memoria_is(tratamento_classificacao: dict, dominio: dict, quantidade, unidade, data, base) -> str:
    """gerarMemoriaCalculoImpostoSeletivo: a memória do IS (o Java a grava no ImpostoSeletivoDomain).

    `dominio` é o ImpostoSeletivoDomain de rr_calculo_is.calcular_is; `quantidade` e `unidade` são as do
    grupo impostoSeletivo do item (quantidade nula vira texto vazio, Objects.toString(q, ""))."""
    fundamentacao, tratamento = _norma_e_tratamento(base, tratamento_classificacao, data)
    valores = {
        "norma": _texto(fundamentacao["textoCurto"]),
        "tratamento": _texto(tratamento["descricao"]),
        "base_calculo": formatar_moeda(dominio.get("vBCIS")),
        "aliquota_ad_valorem": formatar_percentual(dominio.get("pIS")),
        "aliquota_ad_rem": formatar_percentual(dominio.get("pISEspec")),
        "quantidade": "" if quantidade is None else texto_big_decimal(quantidade),
        "unidade": _texto(unidade),
    }
    return _gerar(fundamentacao, valores)
