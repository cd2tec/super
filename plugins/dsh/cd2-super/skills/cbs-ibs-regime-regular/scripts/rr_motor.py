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

Resposta 200: os objetos (com a memória de cálculo de rr_memoria, que rr_calculo.calcular_tributos soma) e o
`total` de rr_saida.totais (ValoresTotaisDomain.create): no /regime-geral, de todos os itens; no
/observabilidade, só dos CALCULADO e CALCULO_SIMULADO, e sem nenhum o `total` some. Os cabeçalhos são os do
controller: X-CALC-APP-VERSION e X-CALC-DB-VERSION (VersaoAplicacaoService.getHeaders) e, quando há aviso,
x-warning-dados-simulados (AvisoDadosSimuladosService.getWarningDadosSimulados, `aviso_dados_simulados`).
O Spring copia os cabeçalhos do ResponseEntity antes de escrever o corpo, então o 500 "Failed to write
request" (um serializer recusou um valor) leva os mesmos cabeçalhos; as respostas de erro dos handlers
não levam nenhum.

Erros: o `type` dos erros globais (ApiExceptionHandler) não tem o /api (HttpUtils.getBaseURL é esquema e
host), e o dos erros de item (ItemCollector, /observabilidade) tem, porque o caminho começa no contexto;
o erro da operação no /observabilidade aponta para #/itens/-1; o `nObj` de cada objeto do
/observabilidade é o `numero` do item ou, sem ele, o índice mais 1.

/regime-geral com vários itens inválidos: o Java processa os itens com parallelStream, e o erro que sobe
é o do primeiro que falhar no tempo; aqui é o primeiro pela ordem dos itens (a bateria só tem casos com
um item inválido nesse endpoint).

Exceção sem código. Como no Java, no /observabilidade qualquer exceção do processamento de um item
(ItemCollector.addErro, ProblemType ERRO_SISTEMA) vira CAL-999 nesse item, com o texto da exceção; no
/regime-geral é o 500 do ApiExceptionHandler.handleGenericException ("Erro de sistema não previsto").
NotImplementedError (CalculoNaoPortado, rr_item.ForaDoEscopo) não entra nessa regra: é ramo sem port, e
sobe, para não virar resposta que a oficial não daria.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

_AQUI = Path(__file__).resolve().parent
if str(_AQUI) not in sys.path:
    sys.path.append(str(_AQUI))

import rr_entrada  # noqa: E402  (precisa do caminho acima)
import rr_calculo  # noqa: E402
import rr_erros  # noqa: E402
import rr_item  # noqa: E402
import rr_saida  # noqa: E402
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


CalculoNaoPortado = rr_calculo.CalculoNaoPortado


def _calcular_item(proc, base):
    """CalculoTributoService.calcular: o tribCalc do item, com os valores Decimal (o TributosDomain antes do
    serializer). O JSON sai só no fim, em `_escrever`, como no Spring, que escreve a resposta inteira."""
    return rr_calculo.calcular_tributos(proc, base)


# AbstractMessageConverterMethodProcessor: HttpMessageNotWritableException vira o ProblemDetail padrão do
# Spring (sem type nem timestamp), nos dois endpoints, mesmo que só um item tenha o valor recusado.
_FALHA_DE_ESCRITA = "Failed to write request"


# AvisoDadosSimuladosService: os CST cujas alíquotas são fictícias em 2026 (TipoWarningDadosSimulados)
_CST_ALIQUOTAS_FICTICIAS = ("010", "220", "221")
CASO_GERAL, CASO_IMPOSTO_SELETIVO, CASO_ALIQUOTAS_FICTICIAS, CASO_CALCULOS_SIMULADOS = 1, 3, 5, 6


def aviso_dados_simulados(op) -> int | None:
    """AvisoDadosSimuladosService.getWarningDadosSimulados: o valor de x-warning-dados-simulados, ou None.

    Compra governamental fica sempre falsa, como no Java (TODO dele), então os casos 2 e 4 não saem."""
    data = op.fato_gerador_aplicavel()
    if data > date(2026, 12, 31):
        return CASO_CALCULOS_SIMULADOS
    ficticias = any(item.cst in _CST_ALIQUOTAS_FICTICIAS for item in op.itens)
    imposto_seletivo = any(item.imposto_seletivo is not None for item in op.itens)
    if ficticias and data > date(2025, 12, 31):
        return CASO_ALIQUOTAS_FICTICIAS
    if data < date(2027, 1, 1):
        return None
    return CASO_IMPOSTO_SELETIVO if imposto_seletivo else CASO_GERAL  # inalcançável: data > 2026 já saiu


def _cabecalhos(op, base) -> dict:
    """Os cabeçalhos do ResponseEntity.ok() dos dois controllers."""
    cabecalhos = dict(base.cabecalhos_versao())
    aviso = aviso_dados_simulados(op)
    if aviso is not None:
        cabecalhos["x-warning-dados-simulados"] = str(aviso)
    return cabecalhos


def _escrever(corpo, cabecalhos, instancia):
    """O Jackson escrevendo a resposta 200 ({objetos, total}): o tribCalc de cada objeto e o total viram o
    JSON dos serializers (rr_saida.serializar), na ordem do @JsonPropertyOrder({oper, objetos, total})."""
    try:
        objetos = [dict(o, tribCalc=rr_saida.serializar(o["tribCalc"])) if "tribCalc" in o else o
                   for o in corpo["objetos"]]
        saida = {"objetos": objetos}
        if corpo.get("total") is not None:
            saida["total"] = rr_saida.serializar(corpo["total"])
    except rr_calculo.EscritaFalhou:
        return 500, {"detail": _FALHA_DE_ESCRITA, "instance": instancia, "status": 500,
                     "title": "Internal Server Error"}, cabecalhos
    return 200, saida, cabecalhos


def _erro_de_sistema(erro: Exception) -> ErroRR:
    """Uma exceção sem ProblemType próprio (ERRO_SISTEMA): o detalhe é o getMessage() dela."""
    return ErroRR(None, str(erro))


def _processar_item(op, item, data, tipo_dfe, base):
    """ProcessamentoItemService.processarItem: o processamento até o tratamento e, depois, o cálculo."""
    proc = rr_item.processar_item(item, op, tipo_dfe, base, data)
    return _calcular_item(proc, base)


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
    objetos = []
    try:
        data, tipo_dfe = _validar_operacao(op, base)
        for item in op.itens:
            if item is None:
                return 500, _sistema(_ITEM_NULO_REGIME_GERAL, base_url, instancia, agora), {}
            trib_calc = _processar_item(op, item, data, tipo_dfe, base)
            # ObjetoDomain {nObj, tribCalc, calculoSimulado}
            objetos.append({"nObj": item.numero, "tribCalc": trib_calc,
                            "calculoSimulado": item.aliquotas_nominais is not None})
    except ErroRR as erro:
        return (*rr_erros.corpo_de_erro(erro, base_url, instancia, agora), {})
    except NotImplementedError:
        raise
    except Exception as erro:  # ApiExceptionHandler.handleGenericException
        return (*rr_erros.corpo_de_erro(_erro_de_sistema(erro), base_url, instancia, agora), {})
    objetos.sort(key=lambda objeto: (objeto["nObj"] is not None, objeto["nObj"] or 0))  # nullsFirst
    # ROCDomain {oper, objetos, total}: oper só com gCompraGov (fora do escopo); total de todos os itens
    total = rr_saida.totais([objeto["tribCalc"] for objeto in objetos])
    return _escrever({"objetos": objetos, "total": total}, _cabecalhos(op, base), instancia)


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
    except NotImplementedError:
        raise
    except Exception as erro:  # ItemCollector(-1): o erro da operação aponta para #/itens/-1
        erro = erro if isinstance(erro, ErroRR) else _erro_de_sistema(erro)
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
        try:  # processarItemComValidacao: a exceção do item fica no item (ItemCollector.addErro)
            trib_calc = _processar_item(op, item, data, tipo_dfe, base)
        except NotImplementedError:
            raise
        except Exception as erro:
            erro = erro if isinstance(erro, ErroRR) else _erro_de_sistema(erro)
            objetos.append({"nObj": n_obj, "estadoItem": erro.estado_item,
                            "erros": [rr_erros.detalhe_de_item(erro, indice, base_url)]})
            continue
        estado = rr_erros.CALCULADO if item.aliquotas_nominais is None else rr_erros.CALCULO_SIMULADO
        objetos.append({"nObj": n_obj, "estadoItem": estado, "tribCalc": trib_calc})
    objetos.sort(key=lambda objeto: objeto["nObj"])  # Stream.sorted: estável
    # ObservabilidadeROCDomain {oper, objetos, total}: o total só dos itens calculados (simulados incluídos)
    total = rr_saida.totais([objeto["tribCalc"] for objeto in objetos
                             if objeto["estadoItem"] in (rr_erros.CALCULADO, rr_erros.CALCULO_SIMULADO)])
    return _escrever({"objetos": objetos, "total": total}, _cabecalhos(op, base), instancia)
