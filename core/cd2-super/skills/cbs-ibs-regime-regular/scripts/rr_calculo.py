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

"""Cálculo dos tributos do item na skill cbs-ibs-regime-regular.

Port (calculadora-rtc-regime-regular, tag 1.7.2) de:
- domain/service/calculotributo/CalculoCbsIbsService.java: `calcular_cbs_ibs` (calcularCbsIbs, com os
  tipos de alíquota, as seis etapas de expressão, gRed, diferimento, tributação regular e monofasia);
- domain/service/calculotributo/CalculoTributoService.java: `calcular_tributos` (calcular, getIBSCBS,
  getGIBSCBS e os getters dos grupos), com TransferenciaCBSService.getPercentualTransferencia;
- domain/model/dto/AliquotaResultadoDTO.java (o construtor do record) e os services de alíquota
  (AliquotaPadraoService, AliquotaReferenciaService, AliquotaAdValoremServicoService,
  PercentualReducaoService, TratamentoTributarioService.buscar,
  ClassificacaoTributariaService.buscarClassificacaoTributariaCalculo);
- api/model/roc/Monofasia*Domain.create e api/model/output/Grupo*MonofasiaOutput.merge.

Estrutura devolvida (para a Task 10 serializar e totalizar). `calcular_tributos` devolve o TributosDomain
como dicts com as chaves e a ordem do @JsonPropertyOrder de cada classe e valores Decimal (o número
como o Java o guarda, antes do serializer); campo nulo não entra no dict (@JsonInclude NON_NULL).
`para_json` faz o papel dos serializers (BigDecimalTDec1302, TDec0302_04 e TDec1104OpRTC): valor com 2
casas, alíquota com 2 a 4, quantidade inteira ou com 4.

Fora do escopo (a skill recusa antes do motor): compra governamental (tpEnteGov e pRedutor chegam nulo
e zero, e os ramos de gTribCompraGov não são portados) e NBS informado. O Imposto Seletivo
(CalculoImpostoSeletivoService) fica em rr_calculo_is.calcular_is, que `calcular_tributos` chama primeiro.
"""
from __future__ import annotations

import re
import sys
from decimal import Decimal
from pathlib import Path

_AQUI = Path(__file__).resolve().parent
if str(_AQUI) not in sys.path:
    sys.path.append(str(_AQUI))

import numeros  # noqa: E402  (precisa do caminho acima)
import rr_base  # noqa: E402
import rr_expressao  # noqa: E402
from rr_erros import ErroRR  # noqa: E402

ZERO = Decimal(0)
UM = Decimal(1)

# CalculoCbsIbsService: as constantes da monofasia (o tipo "Uniforme setorial" com monofasia)
CEM_POR_CENTO = Decimal("100")
PBIO = Decimal("30")
PDIF = Decimal("100")
VARIACAO_EAC = Decimal("5")
ALIQUOTA_EAC = Decimal("0.1922")
ALIQUOTA_GASOLINA_TIPO_A = Decimal("0.7925")

CBS = rr_base.TRIBUTO_CBS
IBS_ESTADUAL = rr_base.TRIBUTO_IBS_ESTADUAL
IBS_MUNICIPAL = rr_base.TRIBUTO_IBS_MUNICIPAL

# application.ibs.enabled no perfil offline: verdadeiro, então o IBS da UF e o do município saem sempre.
CALCULO_IBS_HABILITADO = True


class CalculoNaoPortado(NotImplementedError):
    """Um ramo do cálculo que ainda não foi portado."""


# --- utilitários ---------------------------------------------------------------------------------

def _texto(valor) -> str:
    """String.format("%s") de um objeto Java: o toString, ou "null"."""
    return "null" if valor is None else str(valor)


def _preenchido(texto) -> bool:
    """StringUtils.isNotBlank."""
    return texto is not None and texto.strip() != ""


def _ou_zero(valor):
    """Objects.requireNonNullElse(valor, ZERO)."""
    return ZERO if valor is None else valor


def _mover_direita(valor):
    """BigDecimal.movePointRight(2); nulo continua nulo onde o Java testa antes."""
    return numeros.fracao_para_percentual(valor)


def _publicar_aliquota(fracao):
    """publicarAliquota: a fração em pontos percentuais, arredondada uma vez a 4 casas, de volta a fração."""
    return numeros.percentual_para_fracao(
        numeros.arredondar_aliquota_publicada(numeros.fracao_para_percentual(fracao)))


def _sem_nulos(pares) -> dict:
    """O objeto Java com @JsonInclude(NON_NULL): a ordem é a do @JsonPropertyOrder, e nulo some."""
    return {chave: valor for chave, valor in pares if valor is not None}


def _avaliar(expressao, variaveis):
    """AvaliadorExpressaoAritmetica.evaluate (as entradas nulas já viraram ZERO em `_variaveis`)."""
    if expressao is None:
        raise ErroRR(None, 'Cannot invoke "String.trim()" because "expression" is null')
    return rr_expressao.avaliar(expressao, variaveis)


def _variaveis(**valores) -> dict:
    """O Map.ofEntries do service: cada entrada com requireNonNullElse(v, ZERO)."""
    return {nome: _ou_zero(valor) for nome, valor in valores.items()}


def _consultar(funcao, *args):
    """Uma consulta à base: resultado não único vira o RuntimeException do Spring (sem código, 500)."""
    try:
        return funcao(*args)
    except rr_base.ResultadoNaoUnico as erro:
        raise ErroRR(None, str(erro)) from erro


# --- services de alíquota ------------------------------------------------------------------------

def aliquota_resultado(valor_referencia, valor_padrao, forma_aplicacao) -> Decimal:
    """AliquotaResultadoDTO: o construtor do record (validações e forma de aplicação) e valorAplicavel()."""
    if valor_referencia is None:
        raise ErroRR("CAL-003", "Alíquota de referência não encontrada")
    if valor_referencia < 0:
        raise ErroRR("CAL-005", "Alíquota de referência não pode ter valor negativo")
    if valor_padrao is not None:
        if valor_padrao < 0:
            raise ErroRR("CAL-005", "Alíquota padrão não pode ter valor negativo")
        if forma_aplicacao is None:  # FormaAplicacaoNaoDefinidaException (ErroInternoSistemaException)
            raise ErroRR(None, "Forma de aplicação da alíquota própria não definida",
                         titulo="Forma de Aplicação de Percentual não definida",
                         caminho="forma-aplicacao-percentual-nao-definida")
        if forma_aplicacao == "ACRESCIMO":
            valor_padrao = numeros.totalizar((valor_referencia, valor_padrao))
        elif forma_aplicacao == "DECRESCIMO":
            valor_padrao = numeros.subtrair(valor_referencia, valor_padrao)
        elif forma_aplicacao != "SUBSTITUICAO":  # FormaAplicacaoNaoTratadaException: sem ProblemType próprio
            raise ErroRR(None, "Forma de aplicação da alíquota própria não tratada")
        if valor_padrao < 0:
            raise ErroRR("CAL-005", "Alíquota aplicável resultante não pode ter valor negativo")
    elif forma_aplicacao is not None:
        raise ErroRR("CAL-002", "Alíquota padrão não encontrada")
    return valor_padrao if valor_padrao is not None else valor_referencia


def _buscar_aliquota_padrao(base, id_tributo, codigo_uf, codigo_municipio, data):
    """CalculoCbsIbsService.buscarAliquotaPadrao (AliquotaPadraoService.buscarAliquota)."""
    linha = _consultar(base.buscar_aliquota, id_tributo, codigo_uf, codigo_municipio, data)
    if linha is None:
        raise ErroRR("CAL-001", f"Alíquota de referencia ou padrão não encontrada para o tributo com id {id_tributo}, "
                                f"UF {_texto(codigo_uf)}, município {_texto(codigo_municipio)} e data "
                                f"{data.isoformat()}")
    aplicavel = aliquota_resultado(linha["valorReferencia"], linha["valorPadrao"], linha["formaAplicacao"])
    return numeros.percentual_para_fracao(aplicavel)


def _buscar_aliquota_referencia(base, id_tributo, data):
    """CalculoCbsIbsService.buscarAliquotaReferencia (AliquotaReferenciaService.buscar)."""
    valor = _consultar(base.buscar_aliquota_referencia, id_tributo, data)
    if valor is None:
        raise ErroRR("CAL-003", f"Alíquota de referência não encontrada para o tributo com id {id_tributo} e data "
                                f"{data.isoformat()}")
    return numeros.percentual_para_fracao(valor)


_MULTIPLOS = "Consulta retornou múltiplos valores quando apenas um era esperado."


def _ad_valorem_servico(funcao, *args):
    """AliquotaAdValoremServicoService: IncorrectResultSizeDataAccessException vira ErroGenericoValidacao."""
    try:
        return funcao(*args)
    except rr_base.ResultadoNaoUnico as erro:
        raise ErroRR("REG-023", _MULTIPLOS) from erro


def _buscar_aliquota_uniforme_setorial(base, nbs, id_tributo, id_classificacao, data):
    """CalculoCbsIbsService.buscarAliquotaUniformeSetorial."""
    dto = _ad_valorem_servico(base.buscar_aliquota_ad_valorem_dto, nbs, id_tributo, id_classificacao, data)
    valor = None if dto is None else dto["valor"]
    if valor is None:
        valor = _ad_valorem_servico(base.buscar_aliquota_ad_valorem_por_classificacao_tributaria, id_tributo,
                                    id_classificacao, data)
        if valor is None:
            raise ErroRR("REG-023", f"Alíquota uniforme setorial não encontrada em {data.isoformat()}")
    return numeros.percentual_para_fracao(valor)


def _buscar_aliquota_fixa(base, id_tributo, id_classificacao, data):
    """CalculoCbsIbsService.buscarAliquotaFixa."""
    valor = _ad_valorem_servico(base.buscar_aliquota_ad_valorem_por_classificacao_tributaria, id_tributo,
                                id_classificacao, data)
    if valor is None:
        raise ErroRR("REG-023", f"Alíquota fixa não encontrada em {data.isoformat()}")
    return numeros.percentual_para_fracao(valor)


def _buscar_percentual_reducao(base, id_classificacao, id_tributo, data):
    """PercentualReducaoService.buscar."""
    valor = _consultar(base.buscar_percentual_reducao, id_classificacao, id_tributo, data)
    if valor is None:
        raise ErroRR("CAL-010", f"Percentual de redução não encontrado para classificação tributária com id "
                                f"{id_classificacao} e data {data.isoformat()}")
    return valor


def _buscar_tratamento_tributario(base, id_tratamento):
    """TratamentoTributarioService.buscar."""
    tratamento = _consultar(base.buscar_tratamento_tributario, id_tratamento)
    if tratamento is None:
        raise ErroRR("CAL-009", f"Tratamento tributário com id {id_tratamento} não encontrado")
    return tratamento


def _buscar_classificacao_calculo(base, id_classificacao):
    """ClassificacaoTributariaService.buscarClassificacaoTributariaCalculo."""
    classificacao = _consultar(base.buscar_classificacao_tributaria_calculo, id_classificacao)
    if classificacao is None:
        raise ErroRR("REG-005", f"Classificação tributária não encontrada para id {id_classificacao}")
    return classificacao


def _aliquota_nominal(tributo, item):
    """CalculoCbsIbsService.obterAliquotaNominal."""
    nominais = item.aliquotas_nominais
    if nominais is None:
        return None
    return {CBS: nominais.cbs, IBS_ESTADUAL: nominais.ibs_estadual, IBS_MUNICIPAL: nominais.ibs_municipal}.get(tributo)


def _expressao_usa_aliquota(expressao) -> bool:
    """expressaoUsaAliquota: String.matches(".*\\baliquota\\b.*"), em que o ponto não casa quebra de linha."""
    return _preenchido(expressao) and re.fullmatch(r".*\baliquota\b.*", expressao) is not None


# --- grupos do CbsIbsOutput ----------------------------------------------------------------------

def _grupo_reducao(classificacao, aliquota_efetiva, percentual_reducao, dividida_por_cem):
    """obterGrupoReducao: ReducaoAliquotaDomain {pRedAliq, pAliqEfet}."""
    if percentual_reducao is None or aliquota_efetiva is None or not classificacao["inGrupoReducao"]:
        return None
    return _sem_nulos((
        ("pRedAliq", _mover_direita(percentual_reducao)),
        ("pAliqEfet", _mover_direita(aliquota_efetiva) if dividida_por_cem else aliquota_efetiva),
    ))


def _grupo_desoneracao(cst, c_class_trib, base_calculo, aliquota, percentual_reducao, dividida_por_cem,
                       possui_percentual_reducao):
    """obterGrupoDesoneracao: o TributacaoRegularOutput (a tributação regular de um tributo)."""
    aliquota_efetiva = aliquota
    if possui_percentual_reducao and percentual_reducao is not None:
        aliquota_efetiva = numeros.multiplicar(aliquota, numeros.subtrair(UM, percentual_reducao))
    if dividida_por_cem:
        aliquota_efetiva = _publicar_aliquota(aliquota_efetiva)
    devido = numeros.arredondar_valor(numeros.multiplicar(base_calculo, aliquota_efetiva))
    return {"cst": cst, "cClassTrib": c_class_trib, "baseCalculo": base_calculo,
            "aliquotaEfetiva": _mover_direita(aliquota_efetiva) if dividida_por_cem else aliquota_efetiva,
            "tributoDevido": devido}


def _grupo_diferimento(valor, percentual):
    """obterGrupoDiferimento: DiferimentoDomain {pDif, vDif}."""
    if valor is None or percentual is None:
        return None
    return {"pDif": _mover_direita(percentual), "vDif": valor}


def _fator_biocombustivel():
    """obterFatorBioCombustivel: PBIO / (100 - PBIO)."""
    return numeros.dividir(PBIO, numeros.subtrair(CEM_POR_CENTO, PBIO))


def _grupo_monofasia(classificacao, quantidade, ad_rem_principal, ad_rem_secundaria, calculado, devido,
                     variacao):
    """obterGrupoMonofasia: o GrupoMonofasiaOutput (sempre existe; as etapas podem ser nulas)."""
    padrao = reten = ret = dif = None
    so_ret = classificacao["inGrupoMonofasiaRet"]
    if classificacao["inGrupoMonofasiaPadrao"]:
        padrao = {"quantidade": quantidade if not so_ret else numeros.percentual_para_fracao(
                      numeros.multiplicar(quantidade, variacao)),
                  "aliquotaAdRem": numeros.arredondar_aliquota_publicada(
                      ad_rem_principal if not so_ret else ad_rem_secundaria),
                  "valor": calculado}
    if classificacao["inGrupoMonofasiaReten"]:
        reten = {"quantidade": numeros.multiplicar(quantidade, _fator_biocombustivel()),
                 "aliquotaAdRem": numeros.arredondar_aliquota_publicada(ad_rem_secundaria), "valor": devido}
    if classificacao["inGrupoMonofasiaRet"]:
        ret = {"quantidade": quantidade, "aliquotaAdRem": numeros.arredondar_aliquota_publicada(ad_rem_principal),
               "valor": devido}
    if classificacao["inGrupoMonofasiaDiferimento"]:
        diferido = numeros.subtrair(calculado, devido)
        dif = {"percentualDiferimento": numeros.arredondar_aliquota_publicada(numeros.fracao_para_percentual(
                   numeros.dividir(diferido, calculado))),
               "valorDiferimento": diferido}
    return {"tributoMonofasico": padrao, "tributoSujeitoRetencao": reten, "tributoRetido": ret,
            "tributoDiferido": dif}


# --- CalculoCbsIbsService.calcularCbsIbs ---------------------------------------------------------

def calcular_cbs_ibs(proc, tributo: int, base, imposto_seletivo_calculado: Decimal = ZERO) -> dict | None:
    """CalculoCbsIbsService.calcularCbsIbs: o CbsIbsOutput de um tributo (CBS, IBS da UF ou do município).

    `proc` é o rr_item.ItemProcessado. Devolve None quando a situação tributária do CST do item não tem o
    grupo IBS/CBS (SITR_IND_GIBSCBS falso). O dict tem as chaves do CbsIbsOutput: aliquota, baseCalculo,
    quantidade, tributoCalculado, tributoDevido, grupoReducao, tributacaoRegular, grupoDiferimento,
    grupoMonofasia (compraGovernamental fica de fora: fora do escopo).
    """
    item, data = proc.item, proc.data
    codigo_uf = proc.codigo_uf if tributo == IBS_ESTADUAL else None
    codigo_municipio = proc.codigo_municipio if tributo == IBS_MUNICIPAL else None
    tem_regular = proc.tem_desoneracao
    tratamento_classificacao = proc.tratamento_cbs_ibs

    tratamento = _buscar_tratamento_tributario(base, tratamento_classificacao["idTratamentoTributario"])
    classificacao = _buscar_classificacao_calculo(base, tratamento_classificacao["idClassificacaoTributaria"])
    situacao = base.consultar_situacao_tributaria_por_codigo(item.cst, tributo, data)

    cst = c_class_trib = None
    if tem_regular:
        cst, c_class_trib = item.tributacao_regular.cst, item.tributacao_regular.c_class_trib

    expressao_aliquota = tratamento["expressaoAliquota"]
    expressao_base = tratamento["expressaoBaseCalculo"]
    expressao_efetiva = tratamento["expressaoAliquotaEfetiva"]
    expressao_calculado = tratamento["expressaoTributoCalculado"]
    expressao_devido = tratamento["expressaoTributoDevido"]
    expressao_p_dif = tratamento["expressaoPercentualDiferimento"]
    expressao_v_dif = tratamento["expressaoValorDiferimento"]

    if tratamento["inPossuiAjuste"]:  # ErroFaltaImplementacaoException
        raise ErroRR("CAL-007", f"A classificação tributária {classificacao['codigo']} ainda não possui tratamento "
                                "tributário implementado na Calculadora. Essa funcionalidade está prevista no "
                                "roadmap e será disponibilizada em versão futura.")

    possui_reducao = tratamento["inPossuiPercentualReducao"]
    percentual_reducao = ajuste = redutor = None
    base_informada = proc.base_calculo
    quantidade = proc.quantidade
    is_informado = proc.valor_is_informado if item.imposto_seletivo is not None else None

    padrao_ou_referencia = referencia = ad_valorem = ad_rem = aliquota = None
    ad_rem_principal = ad_rem_secundaria = variacao = p_bio = p_dif = None
    r_aliquota = r_efetiva = r_devido = r_p_dif = r_v_dif = None

    if possui_reducao:
        percentual_reducao = numeros.percentual_para_fracao(
            _buscar_percentual_reducao(base, classificacao["id"], tributo, data))

    tipo_aliquota = "Padrão" if classificacao["codigo"] == "510001" else classificacao["tipoAliquota"]
    informada = _aliquota_nominal(tributo, item)
    informada_aplicada = dividida_por_cem = False
    if tipo_aliquota == "Padrão":
        if informada is not None:
            aliquota, informada_aplicada = numeros.percentual_para_fracao(informada), True
        else:
            aliquota = _buscar_aliquota_padrao(base, tributo, codigo_uf, codigo_municipio, data)
        dividida_por_cem = True
    elif tipo_aliquota == "Uniforme nacional (referência)":
        if informada is not None:
            aliquota, informada_aplicada = numeros.percentual_para_fracao(informada), True
        else:
            aliquota = _buscar_aliquota_referencia(base, tributo, data)
        dividida_por_cem = True
    elif tipo_aliquota == "Uniforme setorial":
        if tratamento["inPossuiMonofasia"]:
            ad_rem_principal, ad_rem_secundaria = ALIQUOTA_GASOLINA_TIPO_A, ALIQUOTA_EAC
            p_bio, p_dif = numeros.percentual_para_fracao(PBIO), numeros.percentual_para_fracao(PDIF)
            variacao = VARIACAO_EAC
        else:
            if informada is not None:
                aliquota, informada_aplicada = numeros.percentual_para_fracao(informada), True
            else:
                aliquota = _buscar_aliquota_uniforme_setorial(base, proc.nbs, tributo, classificacao["id"], data)
            dividida_por_cem = True
    elif tipo_aliquota == "Fixa":
        if informada is not None:
            aliquota, informada_aplicada = numeros.percentual_para_fracao(informada), True
        else:
            aliquota = _buscar_aliquota_fixa(base, tributo, classificacao["id"], data)
        dividida_por_cem = True
    elif tipo_aliquota == "Sem alíquota":
        if informada is not None:
            aliquota, informada_aplicada = numeros.percentual_para_fracao(informada), True
            dividida_por_cem = True
        else:
            aliquota = ZERO
    elif tipo_aliquota == "Alíquotas Combinadas (Ad Valorem e Ad Rem)":
        pass  # "para fazer" no Java
    elif tipo_aliquota is None:  # switch sobre String nula
        raise ErroRR(None, 'Cannot invoke "String.hashCode()" because "<local14>" is null')
    else:
        raise ErroRR("CAL-006", f"Tipo de alíquota ({tipo_aliquota}) desconhecido para cClassTrib "
                                f"{_texto(c_class_trib)} e CST {_texto(cst)}")

    comuns = dict(aliquotaPadraoOuReferencia=padrao_ou_referencia, aliquotaReferencia=referencia,
                  aliquotaAdValorem=ad_valorem, aliquotaAdRem=ad_rem, ajuste=ajuste, redutor=redutor)
    monofasia = dict(aliquotaAdRemPrincipal=ad_rem_principal, aliquotaAdRemSecundaria=ad_rem_secundaria,
                     variacaoPontoPercentual=variacao, pBio=p_bio, percentualDiferimento=p_dif)

    if informada_aplicada and not _expressao_usa_aliquota(expressao_aliquota):
        r_aliquota = aliquota  # a alíquota nominal prevalece sobre uma expressão fixa ("0", "2.08/100")
    elif _preenchido(expressao_aliquota):
        r_aliquota = _avaliar(expressao_aliquota, _variaveis(
            aliquota=aliquota, quantidade=quantidade, percentualReducao=percentual_reducao, **comuns, **monofasia))
    if dividida_por_cem:
        r_aliquota = _publicar_aliquota(r_aliquota)

    if _preenchido(expressao_efetiva):
        r_efetiva = _avaliar(expressao_efetiva, _variaveis(
            quantidade=quantidade, aliquota=r_aliquota, percentualReducao=percentual_reducao,
            pRedutorCompraGov=proc.p_redutor, **comuns, **monofasia))
        if dividida_por_cem:
            r_efetiva = _publicar_aliquota(r_efetiva)

    r_base = _avaliar(expressao_base, _variaveis(
        quantidade=quantidade, aliquota=r_aliquota, aliquotaEfetiva=r_efetiva, impostoSeletivoInformado=is_informado,
        baseCalculoInformada=base_informada, impostoSeletivoCalculado=imposto_seletivo_calculado,
        percentualReducao=percentual_reducao, **comuns, **monofasia))
    if not tratamento["inPossuiMonofasia"]:
        r_base = numeros.arredondar_valor(r_base)  # na monofasia a base é uma quantidade
    if r_base < 0:
        r_base = ZERO

    depois_da_base = dict(quantidade=quantidade, baseCalculo=r_base, aliquota=r_aliquota, aliquotaEfetiva=r_efetiva,
                          baseCalculoInformada=base_informada, impostoSeletivoInformado=is_informado,
                          impostoSeletivoCalculado=imposto_seletivo_calculado, percentualReducao=percentual_reducao,
                          **comuns)
    calculado_exato = _avaliar(expressao_calculado, _variaveis(**depois_da_base, **monofasia))
    r_calculado = numeros.arredondar_valor(calculado_exato)

    if _preenchido(expressao_devido):
        r_devido = numeros.arredondar_valor(_avaliar(expressao_devido, _variaveis(
            tributoCalculado=r_calculado, **depois_da_base, **monofasia)))

    if _preenchido(expressao_p_dif) and _preenchido(expressao_v_dif):
        r_p_dif = _publicar_aliquota(_avaliar(expressao_p_dif, _variaveis(
            tributoCalculado=r_calculado, **depois_da_base)))
        # vDif: a expressão recebe o tributo calculado exato, e o produto é arredondado uma vez
        r_v_dif = numeros.arredondar_valor(_avaliar(expressao_v_dif, _variaveis(
            percentualDiferimento=r_p_dif, tributoCalculado=calculado_exato, **depois_da_base)))
        r_devido = numeros.subtrair(r_calculado, r_v_dif)
        if r_devido < 0:
            r_devido = ZERO

    if situacao is None:  # SituacaoTributaria nula: NullPointerException em getInGrupoIbsCbs
        raise ErroRR(None, 'Cannot invoke "br.gov.serpro.rtc.domain.model.entity.SituacaoTributaria.'
                           'getInGrupoIbsCbs()" because "situacaoTributaria" is null')
    if not situacao["inGrupoIbsCbs"]:
        return None

    reducao = None
    if classificacao["inGrupoReducao"] and not (tem_regular and proc.tp_ente_gov is None):
        reducao = _grupo_reducao(classificacao, r_efetiva, percentual_reducao, dividida_por_cem)
    regular = None
    if tem_regular:
        regular = _grupo_desoneracao(cst, c_class_trib, r_base, r_aliquota, _ou_zero(percentual_reducao),
                                     dividida_por_cem, possui_reducao)
    if tem_regular:
        aliquota_saida = ZERO
    elif dividida_por_cem and r_aliquota is not None:
        aliquota_saida = _mover_direita(r_aliquota)
    else:
        aliquota_saida = r_aliquota
    return {
        "grupoReducao": reducao,
        "tributacaoRegular": regular,
        "grupoDiferimento": _grupo_diferimento(r_v_dif, r_p_dif),
        "grupoMonofasia": _grupo_monofasia(classificacao, quantidade, r_aliquota, r_efetiva, r_calculado, r_devido,
                                           variacao),
        "tributoCalculado": ZERO if tem_regular else r_calculado,
        "tributoDevido": ZERO if tem_regular else r_devido,
        "aliquota": aliquota_saida,
        "baseCalculo": r_base,
        "quantidade": quantidade,
    }


# --- CalculoTributoService.calcular --------------------------------------------------------------

def _possui_aliquota_ibs_informada(item, tributo) -> bool:
    """CalculoTributoService.possuiAliquotaIbsInformada."""
    return _aliquota_nominal(tributo, item) is not None if tributo in (IBS_ESTADUAL, IBS_MUNICIPAL) else False


def calcular_tributos(proc, base) -> dict:
    """CalculoTributoService.calcular: o TributosDomain do item {IS, IBSCBS} (ver a docstring do módulo).

    IS primeiro, depois CBS, IBS da UF e IBS do município. O Java calcula os três em paralelo e, se mais de
    um falha, o allOf().join() relança o primeiro da árvore (CBS, depois UF, depois município): aqui é a
    ordem sequencial. A memória de cálculo é da Task 10.
    """
    item = proc.item
    import rr_calculo_is  # aqui, porque o rr_calculo_is usa os utilitários deste módulo

    imposto_seletivo = None
    if proc.tratamento_is is not None:
        imposto_seletivo = rr_calculo_is.calcular_is(proc, base)
    cbs = ibs_uf = ibs_mun = None
    if proc.tratamento_cbs_ibs is not None:
        # getValorImpostoSeletivo: o vIS publicado, ou zero; entra na base da CBS e do IBS pelas expressões
        is_calculado = rr_calculo_is.valor_imposto_seletivo(imposto_seletivo)
        cbs = calcular_cbs_ibs(proc, CBS, base, is_calculado)
        calcular_uf = (CALCULO_IBS_HABILITADO or _preenchido(proc.nbs)
                       or _possui_aliquota_ibs_informada(item, IBS_ESTADUAL))
        calcular_mun = (CALCULO_IBS_HABILITADO or _preenchido(proc.nbs)
                        or _possui_aliquota_ibs_informada(item, IBS_MUNICIPAL))
        ibs_uf = calcular_cbs_ibs(proc, IBS_ESTADUAL, base, is_calculado) if calcular_uf else None
        ibs_mun = calcular_cbs_ibs(proc, IBS_MUNICIPAL, base, is_calculado) if calcular_mun else None
    ibs_cbs = _ibs_cbs(proc, cbs, ibs_uf, ibs_mun, base)
    return _sem_nulos((("IS", imposto_seletivo), ("IBSCBS", ibs_cbs)))


def _ibs_cbs(proc, cbs, ibs_uf, ibs_mun, base) -> dict:
    """getIBSCBS: IBSCBSDomain. Os grupos ainda não implementados no Java (gTransfCred, gAjusteCompet,
    gEstornoCred, gCredPresOper, gCredPresIBSZFM) e indDoacao saem sempre nulos."""
    monofasia = _monofasia(cbs, ibs_uf, ibs_mun)
    g_ibs_cbs = _g_ibs_cbs(cbs, ibs_uf, ibs_mun, proc.data, base) if monofasia is None else None
    return _sem_nulos((("CST", proc.cst), ("cClassTrib", proc.c_class_trib), ("gIBSCBS", g_ibs_cbs),
                       ("gIBSCBSMono", monofasia)))


def _g_ibs_cbs(cbs, ibs_uf, ibs_mun, data, base) -> dict | None:
    """getGIBSCBS: GrupoIBSCBSDomain {vBC, gIBSUF, gIBSMun, vIBS, gCBS, gTribRegular}."""
    if cbs is None and ibs_uf is None and ibs_mun is None:
        return None  # CST sem gIBSCBS (ex.: imunidade)
    v_bc = next((t["baseCalculo"] for t in (cbs, ibs_uf, ibs_mun) if t is not None), None)
    base.percentual_transferencia(data)  # TransferenciaCBSService: sem linha vigente, RuntimeException
    g_uf = _grupo_tributo(ibs_uf, "pIBSUF", "vIBSUF")
    g_mun = _grupo_tributo(ibs_mun, "pIBSMun", "vIBSMun")
    v_ibs = numeros.totalizar((ZERO if g_uf is None else g_uf.get("vIBSUF"),
                               ZERO if g_mun is None else g_mun.get("vIBSMun")))
    return _sem_nulos((("vBC", v_bc), ("gIBSUF", g_uf), ("gIBSMun", g_mun), ("vIBS", v_ibs),
                       ("gCBS", _grupo_tributo(cbs, "pCBS", "vCBS")),
                       ("gTribRegular", _tributacao_regular(cbs, ibs_uf, ibs_mun))))


def _grupo_tributo(saida, chave_aliquota, chave_valor) -> dict | None:
    """getIBSUF, getIBSMun e getCbs: {p, gDif, gDevTrib, gRed, v}. A devolução de tributos é nula no Java."""
    if saida is None:
        return None
    return _sem_nulos(((chave_aliquota, saida["aliquota"]), ("gDif", saida["grupoDiferimento"]),
                       ("gRed", saida["grupoReducao"]), (chave_valor, saida["tributoDevido"])))


def _tributacao_regular(cbs, ibs_uf, ibs_mun) -> dict | None:
    """getTributacaoRegular: TributacaoRegularDomain; os campos não preenchidos ficam ZERO (@Builder.Default).

    Como no Java, um tributo sem tributação regular depois de outro que tem zera o builder.
    """
    campos = None
    for saida, sufixo in ((cbs, "CBS"), (ibs_uf, "IBSUF"), (ibs_mun, "IBSMun")):
        if saida is None or saida["tributacaoRegular"] is None:
            campos = None
            continue
        regular = saida["tributacaoRegular"]
        if campos is None:
            campos = {"CSTReg": regular["cst"], "cClassTribReg": regular["cClassTrib"]}
        campos[f"pAliqEfetReg{sufixo}"] = regular["aliquotaEfetiva"]
        campos[f"vTribReg{sufixo}"] = regular["tributoDevido"]
    if campos is None:
        return None
    ordem = ("CSTReg", "cClassTribReg", "pAliqEfetRegIBSUF", "vTribRegIBSUF", "pAliqEfetRegIBSMun",
             "vTribRegIBSMun", "pAliqEfetRegCBS", "vTribRegCBS")
    return _sem_nulos((chave, campos.get(chave, None if chave in ("CSTReg", "cClassTribReg") else ZERO))
                      for chave in ordem)


# --- monofasia (MonofasiaDomain e os quatro grupos) ----------------------------------------------

def _etapa(saida, nome):
    return None if saida is None or saida["grupoMonofasia"] is None else saida["grupoMonofasia"][nome]


def _merge_etapa(g1, g2):
    """GrupoEtapaMonofasiaOutput.merge (o município fica de fora no Java: g2 é sempre nulo)."""
    return g2 if g1 is None else g1


def _grupo_etapa(cbs, ibs_uf, nome, chaves) -> dict | None:
    """Monofasia{Padrao,Retencao,RetidoAnteriormente}Domain.create: o IBS só entra com a etapa da UF, e
    sem ela o grupo é nulo mesmo com a CBS (como no Java). `chaves` são (quantidade, adRemIBS, vIBS,
    adRemCBS, vCBS) e a ordem de saída é a do @JsonPropertyOrder de cada classe."""
    q, ad_ibs, v_ibs, ad_cbs, v_cbs = chaves
    campos = None
    etapa_cbs = _etapa(cbs, nome)
    if etapa_cbs is not None:
        campos = {q: etapa_cbs["quantidade"], ad_cbs: etapa_cbs["aliquotaAdRem"], v_cbs: etapa_cbs["valor"]}
    etapa_uf = _merge_etapa(_etapa(ibs_uf, nome), None)
    if etapa_uf is None:
        return None
    campos = campos or {}
    campos.update({q: etapa_uf["quantidade"], ad_ibs: etapa_uf["aliquotaAdRem"], v_ibs: etapa_uf["valor"]})
    return campos


def _ordenar(campos, ordem) -> dict | None:
    if campos is None:
        return None
    return _sem_nulos((chave, campos.get(chave, ZERO)) for chave in ordem)


def _monofasia(cbs, ibs_uf, ibs_mun) -> dict | None:
    """CalculoTributoService.getMonofasia: MonofasiaDomain com os totalizadores da NT 1.30."""
    padrao = _ordenar(_grupo_etapa(cbs, ibs_uf, "tributoMonofasico",
                                   ("qBCMono", "adRemIBS", "vIBSMono", "adRemCBS", "vCBSMono")),
                      ("qBCMono", "adRemIBS", "adRemCBS", "vIBSMono", "vCBSMono"))
    reten = _ordenar(_grupo_etapa(cbs, ibs_uf, "tributoSujeitoRetencao",
                                  ("qBCMonoReten", "adRemIBSReten", "vIBSMonoReten", "adRemCBSReten",
                                   "vCBSMonoReten")),
                     ("qBCMonoReten", "adRemIBSReten", "vIBSMonoReten", "adRemCBSReten", "vCBSMonoReten"))
    ret = _ordenar(_grupo_etapa(cbs, ibs_uf, "tributoRetido",
                                ("qBCMonoRet", "adRemIBSRet", "vIBSMonoRet", "adRemCBSRet", "vCBSMonoRet")),
                   ("qBCMonoRet", "adRemIBSRet", "vIBSMonoRet", "adRemCBSRet", "vCBSMonoRet"))
    dif = None
    dif_uf = _etapa(ibs_uf, "tributoDiferido")
    if dif_uf is not None:  # MonofasiaDiferimentoDomain.create
        campos = {}
        dif_cbs = _etapa(cbs, "tributoDiferido")
        if dif_cbs is not None:
            campos.update({"pDifCBS": dif_cbs["percentualDiferimento"], "vCBSMonoDif": dif_cbs["valorDiferimento"]})
        campos.update({"pDifIBS": dif_uf["percentualDiferimento"], "vIBSMonoDif": dif_uf["valorDiferimento"]})
        dif = _ordenar(campos, ("pDifIBS", "vIBSMonoDif", "pDifCBS", "vCBSMonoDif"))
    if padrao is None and reten is None and ret is None and dif is None:
        return None

    def _valor(grupo, chave):
        return ZERO if grupo is None or grupo.get(chave) is None else grupo[chave]

    tot_ibs = numeros.subtrair(numeros.totalizar((_valor(padrao, "vIBSMono"), _valor(reten, "vIBSMonoReten"))),
                               _valor(dif, "vIBSMonoDif"))
    tot_cbs = numeros.subtrair(numeros.totalizar((_valor(padrao, "vCBSMono"), _valor(reten, "vCBSMonoReten"))),
                               _valor(dif, "vCBSMonoDif"))
    return _sem_nulos((("gMonoPadrao", padrao), ("gMonoReten", reten), ("gMonoRet", ret), ("gMonoDif", dif),
                       ("vTotIBSMonoItem", tot_ibs), ("vTotCBSMonoItem", tot_cbs)))


# --- serialização (os serializers do Java) -------------------------------------------------------

_QUANTIDADES = frozenset({"qBCMono", "qBCMonoReten", "qBCMonoRet", "qTrib"})  # BigDecimalTDec1104OpRTCSerializer
_ALIQUOTAS = frozenset({  # BigDecimalTDec0302_04Serializer
    "pIBSUF", "pIBSMun", "pCBS", "pRedAliq", "pAliqEfet", "pDif", "pAliqEfetRegIBSUF", "pAliqEfetRegIBSMun",
    "pAliqEfetRegCBS", "adRemIBS", "adRemCBS", "adRemIBSReten", "adRemCBSReten", "adRemIBSRet", "adRemCBSRet",
    "pDifIBS", "pDifCBS", "pIS", "pISEspec", "adRemIS",
})


class EscritaFalhou(ValueError):
    """Um serializer do Jackson recusou um valor: a resposta inteira não é escrita, e o Spring devolve o 500
    "Failed to write request" (HttpMessageNotWritableException), nos dois endpoints."""


# Os serializers de texto do ImpostoSeletivoDomain: StringTCSTSerializer, StringTcClassTribSerializer e
# StringUTribSerializer (String.length() em unidades UTF-16)
_TEXTOS = {
    "CSTIS": (lambda t: re.fullmatch(r"[0-9]{3}", t) is not None,
              "TCST deve ser uma string de exatamente 3 dígitos numéricos"),
    "cClassTribIS": (lambda t: re.fullmatch(r"[0-9]{6}", t) is not None,
                     "TcClassTrib deve ser uma string de exatamente 6 dígitos numéricos"),
    "uTrib": (lambda t: 1 <= len(t.encode("utf-16-le")) // 2 <= 6, "uTrib deve ter entre 1 e 6 caracteres"),
}


def _formatar_texto(chave, valor):
    regra = _TEXTOS.get(chave)
    if regra is not None and not regra[0](valor):
        raise EscritaFalhou(regra[1])
    return valor


def _formatar(chave, valor):
    if valor < 0:  # os serializers recusam negativo (IllegalArgumentException)
        raise EscritaFalhou(f"Números negativos não são permitidos ({chave})")
    if chave in _QUANTIDADES:
        return numeros.formatar_quantidade(valor)
    if chave in _ALIQUOTAS:
        return numeros.formatar_aliquota(valor)
    return numeros.formatar_moeda(valor)  # BigDecimalTDec1302Serializer


def para_json(no):
    """O JSON que o Jackson escreve da estrutura de `calcular_tributos`: Decimal vira texto formatado.

    Valor que um serializer recusa levanta EscritaFalhou (o rr_motor responde o 500 do Spring)."""
    if isinstance(no, dict):
        return {chave: (_formatar(chave, valor) if isinstance(valor, Decimal)
                        else _formatar_texto(chave, valor) if isinstance(valor, str) else para_json(valor))
                for chave, valor in no.items()}
    if isinstance(no, list):
        return [para_json(valor) for valor in no]
    return no
