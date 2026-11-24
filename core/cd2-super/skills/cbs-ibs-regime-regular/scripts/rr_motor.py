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

"""Motor da skill cbs-ibs-regime-regular: os dois endpoints de cálculo da calculadora oficial.

Port (calculadora-rtc-regime-regular, tag 1.7.2) de:
- api/controller/CalculadoraTributoController + domain/service/CalculadoraService: `regime_geral`
  (POST /calculadora/regime-geral, com @Valid: Bean Validation é 400 "Campos inválidos.");
- api/controller/ObservabilidadeController + domain/service/ObservabilidadeService: `observabilidade`
  (POST /calculadora/observabilidade/regime-geral, sem @Valid: violação global é 422 com `errors`,
  violação de item fica no item, que sai como INCONSISTENCIA_ENTRADA).

Ordem da operação, nos dois: o JSON (400 se ilegível), o Bean Validation, a UF (tirada do município
quando ausente), o município, a data do fato gerador e o tpDoc. Depois, os itens.

Estado: esta versão responde a entrada (erros de JSON, Bean Validation e da operação). O cálculo de um
item válido chega nas Tasks 7 a 10 do plano e, até lá, `_processar_item` levanta NotImplementedError.
"""
from __future__ import annotations

import sys
from pathlib import Path

_AQUI = Path(__file__).resolve().parent
if str(_AQUI) not in sys.path:
    sys.path.append(str(_AQUI))

import rr_entrada  # noqa: E402  (precisa do caminho acima)
import rr_erros  # noqa: E402
from rr_erros import ErroRR  # noqa: E402

BASE_URL_PADRAO = "http://127.0.0.1"  # HttpUtils.getBaseURL da oficial rodando na máquina (esquema://host)
INSTANCIA_REGIME_GERAL = "/api/calculadora/regime-geral"
INSTANCIA_OBSERVABILIDADE = "/api/calculadora/observabilidade/regime-geral"

_PACOTE = "br.gov.serpro.rtc.api."
_SEM_CORPO_REGIME_GERAL = (
    "Required request body is missing: public org.springframework.http.ResponseEntity<" + _PACOTE
    + "model.roc.ROCDomain> " + _PACOTE + "controller.CalculadoraTributoController.calcularTributos("
    + _PACOTE + "model.input.OperacaoInput)")
_SEM_CORPO_OBSERVABILIDADE = (
    "Required request body is missing: public org.springframework.http.ResponseEntity<" + _PACOTE
    + "model.roc.ObservabilidadeROCDomain> " + _PACOTE
    + "controller.ObservabilidadeController.calcularTributosObservabilidade(" + _PACOTE
    + "model.input.OperacaoInput)")
# A NullPointerException do CalculadoraService.getDetalhesImposto com um elemento nulo em `itens`
_ITEM_NULO_REGIME_GERAL = (
    'Cannot invoke "br.gov.serpro.rtc.api.model.input.ItemOperacaoInput.getNumero()" because "item" is null')
# Validator.validate(null) no ObservabilidadeService.processarItemComValidacao
_ITEM_NULO_OBSERVABILIDADE = ("java.lang.IllegalArgumentException: HV000116: The object to be validated must "
                              "not be null.")


def _ler(corpo, sem_corpo):
    """O corpo lido, ou o detalhe do 400 de JSON ilegível."""
    try:
        return rr_entrada.ler_operacao(corpo), None
    except rr_entrada.CorpoAusente:
        return None, sem_corpo
    except rr_entrada.CorpoIlegivel as erro:
        return None, erro.detalhe


def _validar_operacao(op, base):
    """CalculadoraService.calcularTributos e ObservabilidadeService.processarOperacao, antes dos itens.

    Devolve (data do fato gerador, TipoDfe ou None). Levanta ErroRR.
    """
    if op.uf is None:  # MunicipioService.buscarUfPorMunicipio
        uf = base.buscar_uf_por_municipio(op.municipio)
        if uf is None:
            raise ErroRR("REG-019", f"Município de código {op.municipio} não encontrado")
        op.uf = uf
    if not base.existe_uf(op.uf):  # UfService.validarUf
        raise ErroRR("REG-020", f"UF de sigla {op.uf} não encontrada")
    resultado = base.validar_municipio(op.municipio, op.uf)  # MunicipioService.validarMunicipio
    if resultado == 1:
        raise ErroRR("REG-019", f"Município de código {op.municipio} não encontrado")
    if resultado == 2:
        raise ErroRR("REG-018", f"Município de código {op.municipio} não pertence à UF de sigla {op.uf}")
    data = op.fato_gerador_aplicavel()
    return data, _tipo_dfe(op.tp_doc, data, base)


def _tipo_dfe(tp_doc, data, base):
    """TipoDfeService.buscarPorTipo: None sem tpDoc; REG-028 sem registro vigente; REG-038 sem tratamento."""
    if tp_doc is None:
        return None
    tipo = base.buscar_por_tipo(tp_doc, data)
    if tipo is None:
        raise ErroRR("REG-028", f"Tipo de documento fiscal (tpDoc) de código {tp_doc} não encontrado ou fora de "
                                f"vigência na data {data.isoformat()}")
    if not tipo["possuiTratamento"]:
        raise ErroRR("REG-038", f"Tipo de documento fiscal (tpDoc) de código {tipo['tipo']} ({tipo['sigla']}) não "
                                "possui tratamento implementado na calculadora")
    return tipo


def _processar_item(op, item, data, tipo_dfe, base):
    """ProcessamentoItemService.processarItem: chega nas Tasks 7 a 10."""
    raise NotImplementedError("o cálculo do item ainda não foi portado (Tasks 7 a 10 do plano)")


def _sistema(detalhe, base_url, instancia, agora):
    return rr_erros.corpo_problema(500, rr_erros.TITULO_SISTEMA, rr_erros.CAMINHO_SISTEMA, detalhe, base_url,
                                   instancia, agora)


def regime_geral(corpo: object, base, base_url: str = BASE_URL_PADRAO, agora=None) -> tuple:
    """POST /calculadora/regime-geral: (status, corpo, cabeçalhos). Para no primeiro erro."""
    instancia = INSTANCIA_REGIME_GERAL
    op, ilegivel = _ler(corpo, _SEM_CORPO_REGIME_GERAL)
    if ilegivel is not None:
        return (*rr_erros.corpo_ilegivel(ilegivel, base_url, instancia, agora), {})
    violacoes = rr_entrada.validar_bean(op)
    if violacoes:
        return (*rr_erros.corpo_campos_invalidos(violacoes, base_url, instancia, agora), {})
    try:
        data, tipo_dfe = _validar_operacao(op, base)
        for item in op.itens:
            if item is None:
                return 500, _sistema(_ITEM_NULO_REGIME_GERAL, base_url, instancia, agora), {}
            _processar_item(op, item, data, tipo_dfe, base)
    except ErroRR as erro:
        return (*rr_erros.corpo_de_erro(erro, base_url, instancia, agora), {})
    raise NotImplementedError("a resposta de sucesso do /regime-geral chega na Task 10 do plano")


def observabilidade(corpo: object, base, base_url: str = BASE_URL_PADRAO, agora=None) -> tuple:
    """POST /calculadora/observabilidade/regime-geral: (status, corpo, cabeçalhos). Erro de item fica no item."""
    instancia = INSTANCIA_OBSERVABILIDADE
    op, ilegivel = _ler(corpo, _SEM_CORPO_OBSERVABILIDADE)
    if ilegivel is not None:
        return (*rr_erros.corpo_ilegivel(ilegivel, base_url, instancia, agora), {})
    globais = rr_entrada.validar_global(op)  # validarCamposGlobais: os itens ficam para cada item
    if globais:
        erros = [rr_erros.detalhe_global_de_validacao(v, base_url) for v in globais]
        return (*rr_erros.corpo_erros_calculo(erros, instancia, agora), {})
    try:
        data, tipo_dfe = _validar_operacao(op, base)
    except ErroRR as erro:  # ItemCollector(-1): o erro da operação aponta para #/itens/-1
        return (*rr_erros.corpo_erros_calculo([rr_erros.detalhe_de_item(erro, -1, base_url)], instancia, agora), {})
    if any(item is None for item in op.itens):
        return 500, _sistema(_ITEM_NULO_OBSERVABILIDADE, base_url, instancia, agora), {}
    objetos = []
    for indice, item in enumerate(op.itens):
        n_obj = item.numero if item.numero is not None else indice + 1
        violacoes = rr_entrada.validar_item(item)
        if violacoes:
            objetos.append({"nObj": n_obj, "estadoItem": rr_erros.INCONSISTENCIA_ENTRADA,
                            "erros": [rr_erros.detalhe_de_item(v, indice, base_url) for v in violacoes]})
            continue
        objetos.append(_processar_item(op, item, data, tipo_dfe, base))
    objetos.sort(key=lambda objeto: objeto["nObj"])  # Stream.sorted: estável
    return 200, {"objetos": objetos}, base.cabecalhos_versao()
