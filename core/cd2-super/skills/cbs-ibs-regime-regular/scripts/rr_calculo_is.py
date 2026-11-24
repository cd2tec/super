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


"""Imposto Seletivo do item na skill cbs-ibs-regime-regular.

Port (calculadora-rtc-regime-regular, tag 1.7.2) de
domain/service/calculotributo/CalculoImpostoSeletivoService.java (calcularImpostoSeletivo), com o
ImpostoSeletivoDomain (api/model/roc) que ele monta.

Política numérica (ADR003 do Java): as alíquotas da base (ad valorem e ad rem, em pontos percentuais e
em reais por unidade) são publicadas a 4 casas antes da conta; a ad valorem entra nas expressões como
fração. A base (vBCIS) é a expressão de base arredondada a 2 casas, e o tributo calculado e o devido são
a conta exata sobre esses números publicados, arredondada uma vez a 2 casas. O vIS é o tributo devido; é
ele (ou zero, sem expressão de devido) que entra na base da CBS e do IBS, pela variável
impostoSeletivoCalculado das expressões de CalculoCbsIbsService (ver rr_calculo.calcular_tributos).

Estrutura devolvida: o ImpostoSeletivoDomain como dict com as chaves e a ordem do @JsonPropertyOrder
(CSTIS, cClassTribIS, vBCIS, pIS, pISEspec, adRemIS, uTrib, qTrib, vIS), valores Decimal (o número como o
Java o guarda, antes do serializer) e campo nulo fora do dict (@JsonInclude NON_NULL). A memoriaCalculo
(MemoriaCalculoService.gerarMemoriaCalculoImpostoSeletivo) é da Task 10; rr_calculo.para_json escreve o
JSON. O tributo calculado (antes da expressão de devido) não sai no domínio, como no Java.
"""
from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

_AQUI = Path(__file__).resolve().parent
if str(_AQUI) not in sys.path:
    sys.path.append(str(_AQUI))

import numeros  # noqa: E402  (precisa do caminho acima)
import rr_calculo  # noqa: E402

ZERO = Decimal(0)

# O ImpostoSeletivoDomain.builder() do service: a ordem é a do @JsonPropertyOrder
ORDEM = ("CSTIS", "cClassTribIS", "vBCIS", "pIS", "pISEspec", "adRemIS", "uTrib", "qTrib", "vIS")


def valor_imposto_seletivo(dominio) -> Decimal:
    """ImpostoSeletivoDomain.getValorImpostoSeletivo: o vIS, ou zero (sem IS ou sem vIS)."""
    if dominio is None or dominio.get("vIS") is None:
        return ZERO
    return dominio["vIS"]


def calcular_is(proc, base) -> dict:
    """CalculoImpostoSeletivoService.calcularImpostoSeletivo: o ImpostoSeletivoDomain do item.

    `proc` é o rr_item.ItemProcessado com `tratamento_is` e `aliquota_is` (o rr_item só preenche o
    tratamento do IS com a alíquota e o grupo impostoSeletivo presentes, então os getters do Java que
    dariam NullPointerException aqui não têm caminho).
    """
    seletivo = proc.item.imposto_seletivo
    aliquota_is = proc.aliquota_is
    tratamento = rr_calculo._buscar_tratamento_tributario(base, proc.tratamento_is["idTratamentoTributario"])
    expressao_base = tratamento["expressaoBaseCalculo"]
    expressao_calculado = tratamento["expressaoTributoCalculado"]
    expressao_devido = tratamento["expressaoTributoDevido"]
    # expressaoAliquota e expressaoAliquotaEfetiva estão comentadas no Java: aliquota e aliquotaEfetiva
    # entram nulas (zero nas expressões), como percentualReducao, ajuste, redutor e as de referência.

    quantidade = seletivo.quantidade
    base_informada = seletivo.base_calculo
    is_informado = proc.valor_is_informado

    # alíquotas publicadas (4 casas): são elas que entram na conta
    ad_valorem = numeros.percentual_para_fracao(numeros.arredondar_aliquota_publicada(aliquota_is.ad_valorem))
    ad_rem = numeros.arredondar_aliquota_publicada(aliquota_is.ad_rem)

    comuns = dict(quantidade=quantidade, aliquota=None, aliquotaEfetiva=None, baseCalculoInformada=base_informada,
                  impostoSeletivoInformado=is_informado, impostoSeletivoCalculado=ZERO, percentualReducao=None,
                  aliquotaPadraoOuReferencia=None, aliquotaReferencia=None, aliquotaAdValorem=ad_valorem,
                  aliquotaAdRem=ad_rem, ajuste=None, redutor=None)

    # base publicada (2 casas): é ela que entra na conta
    r_base = numeros.arredondar_valor(rr_calculo._avaliar(expressao_base, rr_calculo._variaveis(**comuns)))
    comuns["baseCalculo"] = r_base
    # conta exata sobre os números publicados, arredondada uma vez
    r_calculado = numeros.arredondar_valor(rr_calculo._avaliar(expressao_calculado,
                                                               rr_calculo._variaveis(**comuns)))
    r_devido = None
    if rr_calculo._preenchido(expressao_devido):
        r_devido = numeros.arredondar_valor(rr_calculo._avaliar(
            expressao_devido, rr_calculo._variaveis(tributoCalculado=r_calculado, **comuns)))

    campos = {
        "CSTIS": seletivo.cst,
        "cClassTribIS": seletivo.c_class_trib,
        "qTrib": quantidade,
        "uTrib": seletivo.unidade,
        "vIS": r_devido,
        "pIS": numeros.fracao_para_percentual(ad_valorem),  # para não enviar a ad valorem dividida por 100
        "pISEspec": ad_rem,  # depreciado no Java, mantido para clientes antigos
        "adRemIS": ad_rem,
        "vBCIS": r_base,
    }
    return rr_calculo._sem_nulos((chave, campos[chave]) for chave in ORDEM)
