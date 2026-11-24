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
"""Saída dos dois endpoints na skill cbs-ibs-regime-regular: os totais e a escrita do JSON.

Port (calculadora-rtc-regime-regular, tag 1.7.2) de:
- api/model/roc/ValoresTotaisDomain.create e o que ele monta (TributosTotaisDomain,
  ImpostoSeletivoTotalDomain, IBSCBSTotalDomain, IBSTotalDomain, IBSUFTotalDomain, IBSMunTotalDomain,
  CBSTotalDomain, MonofasiaTotalDomain, EstornoCreditoDomain), com os acumuladores de api/model/roc/reduce
  (IBSUFTotalAccumulator, IBSMunTotalAccumulator, CBSTotalAccumulator, MonofasiaTotalAccumulator,
  EstornoCreditoAccumulator) e os getters @JsonIgnore de ObjetoDomain, TributosDomain e IBSCBSDomain:
  `totais`;
- os serializers do Jackson na resposta: `serializar` (rr_calculo.para_json, com formatar_moeda,
  formatar_aliquota e formatar_quantidade de numeros.py).

As somas são exatas (BigDecimal.add); a ordem das parcelas não muda o resultado, então o parallelStream
do Java não importa. Os grupos que o Java ainda não preenche (gCredPresOper, gEstornoCred) nunca existem
nos itens, então vCredPres e vCredPresCondSus saem zero e gEstornoCred fica de fora.
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


def serializar(objeto):
    """O JSON que o Jackson escreve de uma estrutura com Decimal (um tribCalc ou um total).

    Levanta rr_calculo.EscritaFalhou quando um serializer recusa um valor."""
    return rr_calculo.para_json(objeto)


def _ou_zero(valor):
    """Objects.requireNonNullElse(valor, ZERO)."""
    return ZERO if valor is None else valor


def _somar(valores) -> Decimal:
    return numeros.totalizar(_ou_zero(v) for v in valores)


def _g(no, *chaves):
    """Navega pelos getters que devolvem null quando o grupo falta."""
    for chave in chaves:
        if no is None:
            return None
        no = no.get(chave)
    return no


def _vdif(grupo):
    """IBSUFDomain/IBSMunDomain/CBSDomain.getVDif: gDif.vDif, ou zero sem gDif."""
    return ZERO if grupo.get("gDif") is None else grupo["gDif"].get("vDif")


def _vdev_trib(grupo):
    """getVDevTrib: gDevTrib.vDevTrib, ou zero sem gDevTrib (o cálculo nunca o preenche)."""
    return ZERO if grupo.get("gDevTrib") is None else grupo["gDevTrib"].get("vDevTrib")


def _total_tributo(grupos, chave_valor) -> dict:
    """IBSUFTotalDomain, IBSMunTotalDomain: {vDif, vDevTrib, v...}, zerado quando nenhum item tem o grupo."""
    return {"vDif": _somar(_vdif(g) for g in grupos), "vDevTrib": _somar(_vdev_trib(g) for g in grupos),
            chave_valor: _somar(g.get(chave_valor) for g in grupos)}


_MONO = (("vIBSMono", "gMonoPadrao"), ("vCBSMono", "gMonoPadrao"), ("vIBSMonoReten", "gMonoReten"),
         ("vCBSMonoReten", "gMonoReten"), ("vIBSMonoRet", "gMonoRet"), ("vCBSMonoRet", "gMonoRet"))


def totais(tributos: list) -> dict | None:
    """ValoresTotaisDomain.create: o `total` da resposta, ou None sem itens a totalizar.

    `tributos` são os tribCalc (TributosDomain em Decimal) dos itens totalizados: todos no /regime-geral;
    no /observabilidade, só os CALCULADO e CALCULO_SIMULADO (sem nenhum, o Java nem chama o create e o
    `total` some)."""
    if not tributos:
        return None
    ibs_cbs = [t["IBSCBS"] for t in tributos if t.get("IBSCBS") is not None]
    grupos = [i["gIBSCBS"] for i in ibs_cbs if i.get("gIBSCBS") is not None]

    impostos = [t["IS"] for t in tributos if t.get("IS") is not None]
    is_total = {"vIS": _somar(i.get("vIS") for i in impostos)} if impostos else None  # getValorImpostoSeletivo

    ufs = [g["gIBSUF"] for g in grupos if g.get("gIBSUF") is not None]
    muns = [g["gIBSMun"] for g in grupos if g.get("gIBSMun") is not None]
    g_ibs = {
        "gIBSUF": _total_tributo(ufs, "vIBSUF"),
        "gIBSMun": _total_tributo(muns, "vIBSMun"),
        "vIBS": _somar(g.get("vIBS") for g in grupos),
        "vCredPres": ZERO,
        "vCredPresCondSus": ZERO,
    }
    cbss = [g["gCBS"] for g in grupos if g.get("gCBS") is not None]
    g_cbs = {"vDif": _somar(_vdif(g) for g in cbss), "vDevTrib": _somar(_vdev_trib(g) for g in cbss),
             "vCBS": _somar(g.get("vCBS") for g in cbss), "vCredPres": ZERO, "vCredPresCondSus": ZERO}
    monos = [i["gIBSCBSMono"] for i in ibs_cbs if i.get("gIBSCBSMono") is not None]
    g_mono = {chave: _somar(_g(m, grupo, chave) for m in monos) for chave, grupo in _MONO}
    ibs_cbs_total = {
        "vBCIBSCBS": _somar(g.get("vBC") for g in grupos if g.get("vBC") is not None),
        "gIBS": g_ibs,
        "gCBS": g_cbs,
        "gMono": g_mono,
    }
    trib_calc = {"IBSCBSTot": ibs_cbs_total} if is_total is None else {"ISTot": is_total,
                                                                       "IBSCBSTot": ibs_cbs_total}
    return {"tribCalc": trib_calc}
