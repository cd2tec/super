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

"""Base de cálculo pelos componentes do item (gComponentesBC) da skill cbs-ibs-regime-regular.

Port (calculadora-rtc-regime-regular, tag 1.7.2) de:
- domain/service/basecalculo/BaseCalculoUnificadaService.java#calcular(ComponentesBaseCalculoInput, int,
  BaseCalculoNatureza, Integer) (L54 a L100): `calcular`;
- api/model/input/basecalculo/enumeration/ElementoBaseCalculo.java: `ELEMENTOS`, `_compoe_base`
  (campoCompoeBase) e `_motivo_nao_composicao` (motivoNaoComposicao).

As bases saem exatas, sem arredondamento: a validação da base declarada compara valores exatos e quem
publica arredonda. O Java percorre `Set.of(values())`, de ordem não determinada; a soma não depende da
ordem, e aqui os avisos saem na ordem de declaração do enum.
"""
from __future__ import annotations

import decimal
import sys
from dataclasses import dataclass, field
from decimal import Context, Decimal
from pathlib import Path

_AQUI = Path(__file__).resolve().parent
if str(_AQUI) not in sys.path:
    sys.path.append(str(_AQUI))

import numeros  # noqa: E402  (precisa do caminho acima)
from rr_erros import ErroRR  # noqa: E402

# BaseCalculoNatureza
BEM = "BEM"
SERVICO = "SERVICO"

# BaseCalculoBase e BaseCalculoOperacao
RG, IS, SN = "RG", "IS", "SN"
SOMA, SUBTRAI = "SOMA", "SUBTRAI"

# Soma exata, como BigDecimal.add (sem MathContext): a escala do resultado é a maior das duas.
_EXATO = Context(prec=decimal.MAX_PREC, rounding=decimal.ROUND_HALF_EVEN, Emin=decimal.MIN_EMIN,
                 Emax=decimal.MAX_EMAX, traps=[decimal.InvalidOperation, decimal.Overflow, decimal.Inexact])


@dataclass(frozen=True)
class Elemento:
    """Uma constante de ElementoBaseCalculo: campo do ComponentesBaseCalculo (rr_entrada) e regras."""
    campo: str
    tag: str
    operacao: str
    bases: frozenset
    natureza: str | None
    ano_inicio: int
    ano_fim: int
    tipos_documento: frozenset


def _e(campo, tag, operacao, bases, natureza, inicio, fim, tipos):
    return Elemento(campo, tag, operacao, frozenset(bases), natureza, inicio, fim, frozenset(tipos))


# ElementoBaseCalculo, na ordem de declaração
ELEMENTOS = (
    _e("v_prod", "vProd", SOMA, (RG, IS, SN), None, 2026, 2033, (55, 65)),
    _e("v_frete", "vFrete", SOMA, (RG, IS, SN), None, 2026, 2033, (55, 65)),
    _e("v_seg", "vSeg", SOMA, (RG, IS, SN), None, 2026, 2033, (55, 65)),
    _e("v_outro", "vOutro", SOMA, (RG, IS, SN), None, 2026, 2033, (55, 65)),
    _e("v_ii", "vII", SOMA, (RG, IS), BEM, 2026, 2033, (55,)),
    _e("v_is", "vIS", SOMA, (RG, SN), None, 2027, 2033, (55, 65)),
    _e("v_desc", "vDesc", SUBTRAI, (RG, IS, SN), None, 2026, 2033, (55, 65)),
    _e("v_pis", "vPIS", SUBTRAI, (RG, IS), None, 2026, 2026, (55, 65)),
    _e("v_cofins", "vCOFINS", SUBTRAI, (RG, IS), None, 2026, 2026, (55, 65)),
    _e("v_icms", "vICMS", SUBTRAI, (RG, IS), BEM, 2026, 2032, (55, 65)),
    _e("v_icms_uf_dest", "vICMSUFDest", SUBTRAI, (RG, IS), BEM, 2026, 2032, (55,)),
    _e("v_fcp", "vFCP", SUBTRAI, (RG, IS), BEM, 2026, 2032, (55, 65)),
    _e("v_fcp_uf_dest", "vFCPUFDest", SUBTRAI, (RG, IS), BEM, 2026, 2032, (55,)),
    _e("v_icms_mono", "vICMSMono", SUBTRAI, (RG, IS), BEM, 2026, 2032, (55, 65)),
    _e("v_issqn", "vISSQN", SUBTRAI, (RG, IS), SERVICO, 2026, 2032, (55, 65)),
)


@dataclass
class BaseCalculoUnificada:
    """BaseCalculoUnificadaOutput: as três bases exatas e os avisos dos campos ignorados."""
    base_rg: Decimal
    base_is: Decimal
    base_sn: Decimal
    avisos: list = field(default_factory=list)


def _compoe_base(elemento: Elemento, ano, natureza, tp_doc) -> bool:
    """ElementoBaseCalculo#campoCompoeBase."""
    ano_valido = elemento.ano_inicio <= ano <= elemento.ano_fim
    natureza_valida = elemento.natureza is None or (natureza is not None and elemento.natureza == natureza)
    return ano_valido and natureza_valida and tp_doc in elemento.tipos_documento


def _motivo_nao_composicao(elemento: Elemento, ano, natureza, tp_doc) -> str | None:
    """ElementoBaseCalculo#motivoNaoComposicao: vigência, natureza e modelo, nessa ordem.

    O texto da vigência traz o traço de intervalo do Java (U+2013), escrito como escape.
    """
    if not elemento.ano_inicio <= ano <= elemento.ano_fim:
        return (f"Campo {elemento.tag} não compõe as bases para o ano {ano} (vigência {elemento.ano_inicio}"
                f"\u2013{elemento.ano_fim}) e foi ignorado no cálculo")
    if not (elemento.natureza is None or (natureza is not None and elemento.natureza == natureza)):
        natureza_texto = "null" if natureza is None else natureza
        return (f"Campo {elemento.tag} não compõe as bases para a natureza {natureza_texto} (aplicável apenas a "
                f"{elemento.natureza}) e foi ignorado no cálculo")
    if tp_doc not in elemento.tipos_documento:
        return f"Campo {elemento.tag} não compõe as bases para o documento modelo {tp_doc} e foi ignorado no cálculo"
    return None


def calcular(componentes, ano: int, natureza: str | None, tp_doc: int | None) -> BaseCalculoUnificada:
    """BaseCalculoUnificadaService#calcular(componentes, ano, natureza, tpDoc), L54 a L100.

    `componentes` é o rr_entrada.ComponentesBaseCalculo; `natureza` é BEM, SERVICO ou None. tpDoc fora de
    55 e 65 (ou ausente) é CampoInvalidoException (REG-024, HTTP 400).
    """
    if tp_doc is None or tp_doc not in (55, 65):
        raise ErroRR("REG-024", "Tipo de documento deve ser 55 (NF-e) ou 65 (NFC-e)")
    soma = {RG: Decimal(0), IS: Decimal(0), SN: Decimal(0)}
    subtrai = {RG: Decimal(0), IS: Decimal(0), SN: Decimal(0)}
    avisos = []
    for elemento in ELEMENTOS:
        valor = getattr(componentes, elemento.campo)
        if not _compoe_base(elemento, ano, natureza, tp_doc):
            if valor is not None and valor != 0:
                motivo = _motivo_nao_composicao(elemento, ano, natureza, tp_doc)
                if motivo is not None:
                    avisos.append(motivo)
            continue
        if valor is None or valor == 0:
            continue
        alvo = soma if elemento.operacao == SOMA else subtrai
        for base in (RG, IS, SN):
            if base in elemento.bases:
                alvo[base] = _EXATO.add(alvo[base], valor)
    return BaseCalculoUnificada(numeros.subtrair(soma[RG], subtrai[RG]), numeros.subtrair(soma[IS], subtrai[IS]),
                                numeros.subtrair(soma[SN], subtrai[SN]), avisos)
