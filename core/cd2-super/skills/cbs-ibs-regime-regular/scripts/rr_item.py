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

"""Processamento do item até o tratamento tributário, na skill cbs-ibs-regime-regular.

Port (calculadora-rtc-regime-regular, tag 1.7.2) de domain/service/ProcessamentoItemService.java:
`processar_item` é processarItem (L89 a L140) até a chamada de CalculoTributoService.calcular, que fica
para o motor (Tasks 8 a 10); `_obter_tratamento_classificacao` é obterTratamentoClassificacao (L142 a
L285). Vêm junto os services que essas linhas chamam e que levantam as exceções de negócio:
ClassificacaoTributariaService, TratamentoClassificacaoService, SituacaoTributariaService.validarCst,
TipoDfeClassificacaoService.validarVinculo, ValidacaoNomenclaturaService (modo PERMISSIVO),
NcmAplicavelService, NbsAplicavelService (só a parte que dispensa NBS informado), UfService.buscar e
RedutorCompraGovernamentalService.buscarValorRedutor (sem tpEnteGov, zero).

A ordem das verificações é a do Java: a primeira que falha decide o erro (uma ErroRR com o código do
ProblemType da exceção Java). Como no Java, o item é alterado no lugar: quantidade e unidade padrão,
NCM zerado anulado (ajustarNcm), base de cálculo derivada dos componentes ou do Imposto Seletivo.

Fora do escopo da skill (a recusa é do calc.py, antes do motor): NBS informado e gCompraGov. Um NBS
não vazio que passe da verificação de NCM e NBS simultâneos levanta ForaDoEscopo.
"""
from __future__ import annotations

import decimal
import sys
from dataclasses import dataclass
from datetime import date
from decimal import Context, Decimal
from pathlib import Path

_AQUI = Path(__file__).resolve().parent
if str(_AQUI) not in sys.path:
    sys.path.append(str(_AQUI))

import numeros  # noqa: E402  (precisa do caminho acima)
import rr_base  # noqa: E402
import rr_base_calculo  # noqa: E402
from rr_erros import ErroRR  # noqa: E402

ANO_LIMITE_SUPERIOR_ALIQUOTAS_NOMINAIS = 2027
CBS_E_IBS = "CBS e IBS"
IMPOSTO_SELETIVO = "Imposto Seletivo"
_TRIO_RIGIDO = frozenset({"NFE", "NFCE", "NFSE"})  # ValidacaoNomenclaturaService.TRIO_RIGIDO

# BigDecimal.add sem MathContext: exata, com a maior escala das duas parcelas.
_EXATO = Context(prec=decimal.MAX_PREC, rounding=decimal.ROUND_HALF_EVEN, Emin=decimal.MIN_EMIN,
                 Emax=decimal.MAX_EMAX, traps=[decimal.InvalidOperation, decimal.Overflow, decimal.Inexact])


class ForaDoEscopo(NotImplementedError):
    """Um caminho do Java que a skill não porta (NBS informado). O calc.py recusa antes de chegar aqui."""


@dataclass
class AliquotaImpostoSeletivo:
    """AliquotaImpostoSeletivoModel: a alíquota do IS resolvida (informada ou da base)."""
    ad_valorem: Decimal | None = None
    ad_rem: Decimal | None = None
    unidade_medida: str | None = None


@dataclass
class ItemProcessado:
    """O que processarItem entrega ao CalculoTributoService.calcular (OperacaoModel e
    TratamentoClassificacaoModel), com atalhos para quem consome.

    Consumidores (Tasks 8 a 10):
    - CalculoTributoService.calcular: `tratamento_cbs_ibs`, `tratamento_is`, `tem_desoneracao`,
      `tratamento_cbs_ibs_desoneracao` (`tratamento_efetivo` é o que ele usa), `aliquota_is`, `data`,
      `codigo_uf`, `codigo_municipio`, `tp_ente_gov`, `p_redutor`, `nbs`, `item.numero`, `cst` e
      `c_class_trib` (o CST e a cClassTrib do item, que saem no gIBSCBS), `item.aliquotas_nominais`;
    - CalculoCbsIbsService.calcularCbsIbs: `base_calculo`, `quantidade`, `unidade`,
      `valor_is_informado`, `item.tributacao_regular` (o CST e a cClassTrib efetivos, com desoneração),
      `item.aliquotas_nominais`;
    - CalculoImpostoSeletivoService: `item.imposto_seletivo` (cst, cClassTrib, unidade, quantidade),
      `base_calculo_is`, `valor_is_informado`, `aliquota_is`;
    - o motor: `calculo_simulado` (estadoItem CALCULO_SIMULADO e calculoSimulado do /regime-geral).

    Os dicts de tratamento são o TratamentoClassificacaoDTO de rr_base.buscar_tratamento_classificacao:
    {idTratamentoTributario, idClassificacaoTributaria, inIncompativelComSuspensao, inExigeGrupoDesoneracao}.
    """
    item: object
    data: date
    codigo_municipio: int
    codigo_uf: int
    ncm: str | None
    nbs: str | None
    tratamento_cbs_ibs: dict
    tratamento_is: dict | None
    tratamento_cbs_ibs_desoneracao: dict | None
    aliquota_is: AliquotaImpostoSeletivo | None
    tem_desoneracao: bool
    tp_ente_gov: object = None
    p_redutor: Decimal = Decimal(0)

    @property
    def tratamento_efetivo(self) -> dict:
        """CalculoTributoService.calcular: o da desoneração quando há, senão o de CBS e IBS."""
        return self.tratamento_cbs_ibs_desoneracao if self.tem_desoneracao else self.tratamento_cbs_ibs

    @property
    def base_calculo(self) -> Decimal | None:
        return self.item.base_calculo

    @property
    def quantidade(self) -> Decimal:
        return self.item.quantidade

    @property
    def unidade(self) -> str:
        return self.item.unidade

    @property
    def cst(self) -> str:
        return self.item.cst

    @property
    def c_class_trib(self) -> str:
        return self.item.c_class_trib

    @property
    def base_calculo_is(self) -> Decimal | None:
        seletivo = self.item.imposto_seletivo
        return None if seletivo is None else seletivo.base_calculo

    @property
    def valor_is_informado(self) -> Decimal:
        """ImpostoSeletivoInput.getValorImpostoSeletivoInformado (zero sem o valor); zero sem o grupo."""
        return _valor_is_informado(self.item.imposto_seletivo)

    @property
    def calculo_simulado(self) -> bool:
        return self.item.aliquotas_nominais is not None


# --- utilitários (StringUtils e os do service) -----------------------------------------------------

def _vazio(texto) -> bool:
    """ProcessamentoItemService.isNullOrEmpty: nulo ou só espaço (String.trim)."""
    return texto is None or texto.strip(_TRIM) == ""


_TRIM = "".join(chr(c) for c in range(0x21))  # String.trim tira todo caractere <= U+0020


def _em_branco(texto) -> bool:
    """StringUtils.isBlank: nulo, vazio ou só espaço em branco (Character.isWhitespace)."""
    return texto is None or texto.strip() == ""


def _tamanho(texto) -> int:
    """StringUtils.length: 0 para nulo; tamanho em unidades UTF-16."""
    return 0 if texto is None else len(texto.encode("utf-16-le")) // 2


def _somar(a: Decimal, b: Decimal) -> Decimal:
    return _EXATO.add(a, b)


def _valor_is_informado(seletivo) -> Decimal:
    if seletivo is None or seletivo.imposto_informado is None:
        return Decimal(0)
    return seletivo.imposto_informado


def _nomenclatura(ncm, nbs):
    """O par (tipo, código) das mensagens de Imposto Seletivo: NCM quando não está em branco, senão NBS."""
    return ("NCM", ncm) if not _em_branco(ncm) else ("NBS", nbs)


def _texto(valor) -> str:
    """String.format("%s") de um objeto Java: BigDecimal.toString, ou "null"."""
    return "null" if valor is None else str(valor)


def _consultar(funcao, *args):
    """Uma consulta à base; as exceções do rr_base viram as do Java (sem código no ProblemType, 500)."""
    try:
        return funcao(*args)
    except rr_base.ClassificacaoTributariaNaoEncontradaException as erro:
        raise ErroRR("REG-005", str(erro)) from erro
    except (rr_base.ResultadoNaoUnico, rr_base.ValidacaoException) as erro:
        raise ErroRR(None, str(erro)) from erro


# --- services --------------------------------------------------------------------------------------

def _buscar_classificacao(base, codigo, id_tributo, data, tipo) -> dict:
    """ClassificacaoTributariaService.buscarClassificacaoTributaria (CbsIbs: 2; ImpostoSeletivo: 1)."""
    classificacao = _consultar(base.buscar_classificacao_tributaria, codigo, id_tributo, data)
    if classificacao is None:
        raise ErroRR("REG-005", f"Classificação tributária não encontrada para código {_texto(codigo)} e data "
                                f"{data.isoformat()} ({tipo})")
    return classificacao


def _buscar_tratamento(base, id_classificacao, data) -> dict:
    """TratamentoClassificacaoService.buscarTratamentoClassificacao."""
    tratamento = _consultar(base.buscar_tratamento_classificacao, id_classificacao, data)
    if tratamento is None:
        raise ErroRR("REG-008", f"Tratamento tributário não encontrado para classificação tributária com id "
                                f"{id_classificacao} e data {data.isoformat()}")
    return tratamento


def _validar_cst(base, cst, id_tributo, data):
    """SituacaoTributariaService.validarCst."""
    if not base.existe_cst(cst, id_tributo, data):
        tributo = IMPOSTO_SELETIVO if id_tributo == rr_base.TRIBUTO_IS else CBS_E_IBS
        raise ErroRR("REG-004", f"Situação tributária (CST) não encontrada para código {_texto(cst)} e data "
                                f"{data.isoformat()} ({tributo})")


def _validar_vinculo_tipo_dfe(base, tipo_dfe, classificacao, data):
    """ProcessamentoItemService.validarVinculoTipoDfe (L292 a L299) e
    TipoDfeClassificacaoService.validarVinculo."""
    if tipo_dfe is None:
        return
    vinculos = _consultar(base.buscar_tipo_dfe_classificacao, classificacao["id"], data)
    if not any(v["tipoDfe"]["id"] == tipo_dfe["id"] for v in vinculos):
        raise ErroRR("REG-029", f"Classificação Tributária de código {classificacao['codigo']} não vinculada ao "
                                f"tipo de documento fiscal (tpDoc) {tipo_dfe['tipo']} ({CBS_E_IBS})")


def _criticar_ncm(base, ncm, id_classificacao, codigo, data, tributos, tem_anexo):
    """NcmAplicavelService.criticar: rejeita quando !A ou quando (E e !S)."""
    if not tem_anexo:
        return
    if not base.tem_ncm_aplicavel(ncm, id_classificacao, data) or (
            base.tem_excecao_ncm_aplicavel(ncm, id_classificacao, data)
            and not base.tem_ncm_aplicavel_sem_excecao(ncm, id_classificacao, data)):
        raise ErroRR("REG-011", f"NCM de código {ncm} não vinculada à Classificação Tributária de código "
                                f"{codigo} ({tributos})")


def _validar_ncm_aplicavel(base, ncm, id_classificacao, codigo, data, tributos):
    """NcmAplicavelService.validarNcmAplicavel: com anexo, exige NCM completo e o critica."""
    completo = _tamanho(ncm) == 8
    tem_anexo = base.tem_ncm_aplicavel_para_classificacao(id_classificacao, data)
    if tem_anexo and not completo:
        raise ErroRR("REG-015", "NCM completo não informado")
    if completo:
        _criticar_ncm(base, ncm, id_classificacao, codigo, data, tributos, tem_anexo)


def _criticar_ncm_contra_anexo(base, ncm, id_classificacao, codigo, data, tributos):
    """NcmAplicavelService.criticarNcmContraAnexo."""
    _criticar_ncm(base, ncm, id_classificacao, codigo, data, tributos,
                  base.tem_ncm_aplicavel_para_classificacao(id_classificacao, data))


def _validar_nbs_aplicavel(base, nbs, id_classificacao, codigo, data, tributos):
    """NbsAplicavelService.validarNbsAplicavel. Só o caminho de NBS vazio: com anexo, NBS incompleta."""
    completo = _tamanho(nbs) == 9
    tem_anexo = base.tem_nbs_aplicavel_para_classificacao(id_classificacao, data)
    if tem_anexo and not completo:
        raise ErroRR("REG-016", "NBS completo não informado")
    if completo:  # NbsAplicavelService.criticar: precisa de NBS informada
        raise ForaDoEscopo("NBS informada: a crítica contra o anexo de NBS não foi portada")


def _validar_nomenclatura_tp_doc(base, sigla_dfe, classificacao, ncm, nbs, data):
    """ValidacaoNomenclaturaService.validar, no modo PERMISSIVO fixado no call site (L202 a L204)."""
    enum = _consultar(rr_base.sigla_dfe_por_normalizada, sigla_dfe)  # SiglasDFeEnum.getPorSiglaNormalizada
    sigla = rr_base.SIGLAS_DFE[enum]
    id_cl, codigo = classificacao["id"], classificacao["codigo"]
    resultado = _consultar(base.determinar_nomenclatura, enum, codigo, data)
    if resultado in ("NCM", "EXCECAO_NCM"):  # exigirNcm
        if not _em_branco(nbs):
            raise ErroRR("REG-030", f"NBS não permitida para a Classificação Tributária de código {codigo} no "
                                    f"tipo de documento {sigla}")
        if _tamanho(ncm) != 8:
            raise ErroRR("REG-015", "NCM completo não informado")
        _criticar_ncm_contra_anexo(base, ncm, id_cl, codigo, data, CBS_E_IBS)
    elif resultado in ("NBS", "EXCECAO_NBS"):  # exigirNbs
        if not _em_branco(ncm):
            raise ErroRR("REG-030", f"NCM não permitida para a Classificação Tributária de código {codigo} no "
                                    f"tipo de documento {sigla}")
        if _tamanho(nbs) != 9:
            raise ErroRR("REG-016", "NBS completo não informado")
        raise ForaDoEscopo("NBS informada: a crítica contra o anexo de NBS não foi portada")
    elif resultado == "MISTO":  # exigirNcmOuNbs
        if not _em_branco(ncm):
            if _tamanho(ncm) != 8:
                raise ErroRR("REG-015", "NCM completo não informado")
            _criticar_ncm_contra_anexo(base, ncm, id_cl, codigo, data, CBS_E_IBS)
        elif not _em_branco(nbs):
            if _tamanho(nbs) != 9:
                raise ErroRR("REG-016", "NBS completo não informado")
            raise ForaDoEscopo("NBS informada: a crítica contra o anexo de NBS não foi portada")
        else:
            raise ErroRR("REG-033", f"A Classificação Tributária de código {codigo} exige NCM ou NBS completo "
                                    f"para o tipo de documento {sigla}")
    else:  # SEM: validarSemExigencia
        ncm_informado, nbs_informado = not _em_branco(ncm), not _em_branco(nbs)
        if not ncm_informado and not nbs_informado:
            return
        if enum in _TRIO_RIGIDO:
            if enum == "NFSE":
                if ncm_informado:
                    raise ErroRR("REG-030", f"NCM não permitida para a Classificação Tributária de código "
                                            f"{codigo} no tipo de documento {sigla}")
            elif nbs_informado:
                raise ErroRR("REG-030", f"NBS não permitida para a Classificação Tributária de código {codigo} "
                                        f"no tipo de documento {sigla}")
            return
        # PERMISSIVO
        if ncm_informado:
            _validar_ncm_aplicavel(base, ncm, id_cl, codigo, data, CBS_E_IBS)
        else:
            _validar_nbs_aplicavel(base, nbs, id_cl, codigo, data, CBS_E_IBS)


# --- ProcessamentoItemService ------------------------------------------------------------------------

def _ajustar_ncm(item, tipo_dfe):
    """ItemOperacaoInput.ajustarNcm: NCM "00" ou "00000000" vira nulo no documento que permite NCM zero."""
    if tipo_dfe is not None and tipo_dfe["permiteNcmZero"] and item.ncm in ("00", "00000000"):
        item.ncm = None


def _inferir_natureza(item):
    """ProcessamentoItemService.inferirNatureza (L379 a L392)."""
    possui_ncm, possui_nbs = not _em_branco(item.ncm), not _em_branco(item.nbs)
    if possui_ncm and possui_nbs:
        return None
    if possui_ncm:
        return rr_base_calculo.BEM
    if possui_nbs:
        return rr_base_calculo.SERVICO
    return None


def _texto_base_componentes(valor: Decimal) -> str:
    """BaseCalculoComponentesInconsistenteException.texto: 2 casas quando cabe, senão todas (toPlainString)."""
    expoente = valor.normalize(_EXATO).as_tuple().exponent if valor != 0 else 0
    if -expoente <= numeros.ESCALA_VALOR:
        return numeros.formatar_moeda(valor)
    return format(valor, "f")


def _base_componentes_inconsistente(declarada, calculada):
    return ErroRR("REG-034", f"A base de cálculo declarada ({_texto_base_componentes(declarada)}) diverge da base "
                             f"calculada a partir dos componentes informados ({_texto_base_componentes(calculada)})")


def _validar_base_calculo_com_componentes(op, item, componentes, data):
    """ProcessamentoItemService.validarBaseCalculoComComponentes (L338 a L377)."""
    resultado = rr_base_calculo.calcular(componentes, data.year, _inferir_natureza(item), op.tp_doc)
    seletivo = item.imposto_seletivo
    if seletivo is not None:
        calculada = resultado.base_is
        if calculada < 0:
            raise ErroRR("REG-023", "A base de cálculo do Imposto Seletivo calculada a partir dos componentes é "
                                    "negativa")
        if seletivo.base_calculo is not None and seletivo.base_calculo != calculada:
            raise _base_componentes_inconsistente(seletivo.base_calculo, calculada)
        if seletivo.base_calculo is None:
            seletivo.base_calculo = calculada
        return
    calculada = resultado.base_rg
    if calculada < 0:
        raise ErroRR("REG-023", "A base de cálculo calculada a partir dos componentes é negativa")
    if item.base_calculo is not None and item.base_calculo != calculada:
        raise _base_componentes_inconsistente(item.base_calculo, calculada)
    if item.base_calculo is None:
        item.base_calculo = calculada


def _validar_base_calculo_legado(item):
    """ProcessamentoItemService.validarBaseCalculoLegado (L394 a L424)."""
    seletivo = item.imposto_seletivo
    base_calculo = item.base_calculo
    if seletivo is None:
        if base_calculo is None:
            raise ErroRR("REG-025", "A base de cálculo de CBS e IBS deve ser informada quando o grupo do Imposto "
                                    "Seletivo não é informado")
        return
    base_is = seletivo.base_calculo
    if base_is is None:
        raise ErroRR("REG-023", "A base de cálculo do Imposto Seletivo deve ser informada quando o grupo de "
                                "Imposto Seletivo é informado")
    informado = seletivo.imposto_informado
    if base_calculo is None:
        item.base_calculo = _somar(base_is, _valor_is_informado(seletivo))
        return
    if informado is not None:
        if base_calculo != _somar(base_is, informado):
            raise ErroRR("REG-026", f"A base de cálculo de CBS e IBS informada {_texto(base_calculo)} difere da "
                                    f"soma da base de cálculo do Imposto Seletivo {_texto(base_is)} com o Imposto "
                                    f"Seletivo informado {_texto(informado)}")
    elif base_calculo < base_is:
        raise ErroRR("REG-027", f"A base de cálculo de CBS e IBS informada {_texto(base_calculo)} é menor que a "
                                f"base de cálculo do Imposto Seletivo {_texto(base_is)}")


def _validar_ncm_nbs(base, ncm, nbs, data):
    """ProcessamentoItemService.validarNcmNbs (L605 a L619)."""
    if not _vazio(ncm) and not _vazio(nbs):
        raise ErroRR("REG-001", "Não é permitido informar NCM e NBS simultaneamente para um item")
    if not _vazio(ncm) and not base.existe_ncm(ncm, data):
        raise ErroRR("REG-002", f"NCM de código {ncm} não encontrada para a data {data.isoformat()}")
    if not _vazio(nbs):
        raise ForaDoEscopo("NBS informada: NbsService.existeNbs não foi portado")


def _informou_aliquotas_nominais(aliquotas) -> bool:
    """ProcessamentoItemService.informouAliquotasNominais (L476 a L484)."""
    if aliquotas is None:
        return False
    seletivo = aliquotas.imposto_seletivo
    return (aliquotas.cbs is not None or aliquotas.ibs_estadual is not None or aliquotas.ibs_municipal is not None
            or (seletivo is not None and (seletivo.ad_valorem is not None or seletivo.ad_rem is not None)))


def _validar_aliquotas_nominais(base, item, data):
    """ProcessamentoItemService.validarAliquotasNominais (L454 a L474)."""
    if data.year < ANO_LIMITE_SUPERIOR_ALIQUOTAS_NOMINAIS:
        if _informou_aliquotas_nominais(item.aliquotas_nominais):
            raise ErroRR("REG-036", "As alíquotas nominais não podem ser informadas para operações com fato "
                                    "gerador anterior a 01/01/2027, pois a calculadora resolve as alíquotas vigentes")
        return
    situacao = base.consultar_situacao_tributaria_por_codigo(item.cst, rr_base.TRIBUTO_CBS, data)
    if situacao is None or situacao["inGrupoIbsCbs"] is not True:
        return
    aliquotas = item.aliquotas_nominais
    if (aliquotas is None or aliquotas.cbs is None or aliquotas.ibs_estadual is None
            or aliquotas.ibs_municipal is None):
        raise ErroRR("REG-037", "As alíquotas nominais (os três campos cbs, ibsEstadual e ibsMunicipal) devem ser "
                                "informadas para operações com fato gerador a partir de 01/01/2027")


def _validar_classificacao_cbs_ibs(classificacao, cst, ncm, nbs):
    """ProcessamentoItemService.validarClassificacaoTributariaCbsIbs (L582 a L597)."""
    if _vazio(ncm) and _vazio(nbs):
        return
    if classificacao["cst"] != cst:
        raise ErroRR("REG-006", f"Classificação tributária (cClassTrib) de código {classificacao['codigo']} não "
                                f"está vinculada à situação tributária (CST) de código {_texto(cst)} ({CBS_E_IBS})")


def _validar_classificacao_is(classificacao, cst):
    """ProcessamentoItemService.validarClassificacaoTributariaImpostoSeletivo (L599 a L603)."""
    if classificacao["cst"] != cst:
        raise ErroRR("REG-006", f"Classificação tributária (cClassTrib) de código {classificacao['codigo']} não "
                                f"está vinculada à situação tributária (CST) de código {_texto(cst)} "
                                f"({IMPOSTO_SELETIVO})")


def _existe_incidencia_is(base, ncm, nbs, data) -> bool:
    """ProcessamentoItemService.existeIncidenciaImpostoSeletivo (L491 a L500)."""
    if not _em_branco(ncm) and _tamanho(ncm) == 8:
        return (base.existe_ncm_ad_valorem(ncm, rr_base.TRIBUTO_IS, data)
                or base.buscar_aliquota_ad_rem(ncm, rr_base.TRIBUTO_IS, data) is not None)
    if not _em_branco(nbs) and _tamanho(nbs) == 9:
        raise ForaDoEscopo("NBS informada: existeNbsAdValorem não foi portado")
    return False


def _analisar_aliquota_is(base, item, data) -> AliquotaImpostoSeletivo | None:
    """ProcessamentoItemService.analisarAliquotaImpostoSeletivo (L502 a L564) e
    obterAliquotasNominaisImpostoSeletivo (L566 a L576)."""
    ncm, nbs = item.ncm, item.nbs
    seletivo = item.imposto_seletivo
    informadas = None
    if seletivo is not None and item.aliquotas_nominais is not None:
        informadas = item.aliquotas_nominais.imposto_seletivo
    if informadas is not None and informadas.ad_rem is not None and _em_branco(seletivo.unidade):
        raise ErroRR("REG-023", "A unidade de medida do Imposto Seletivo deve ser informada quando a alíquota ad "
                                "rem é informada pelo usuário")
    ad_valorem = informadas.ad_valorem if informadas is not None else None
    ad_rem = informadas.ad_rem if informadas is not None else None
    unidade = seletivo.unidade if ad_rem is not None else None
    if not _em_branco(ncm) and _tamanho(ncm) == 8:
        if ad_valorem is None:
            dto = base.buscar_aliquota_ad_valorem(ncm, rr_base.TRIBUTO_IS, data)
            if dto is not None:
                ad_valorem = dto["valor"]
        if ad_rem is None:
            dto = base.buscar_aliquota_ad_rem(ncm, rr_base.TRIBUTO_IS, data)
            if dto is not None:
                ad_rem = dto["valor"]
                unidade = dto["unidadeMedida"]
    elif not _em_branco(nbs) and _tamanho(nbs) == 9:
        if ad_valorem is None:
            raise ForaDoEscopo("NBS informada: a alíquota ad valorem de serviço não foi portada")
    if ad_valorem is not None or ad_rem is not None:
        return AliquotaImpostoSeletivo(ad_valorem, ad_rem, unidade)
    return None


def _validar_quantidade_e_unidade(item, aliquota):
    """ProcessamentoItemService.validarQuantidadeEUnidade (L426 a L446)."""
    seletivo = item.imposto_seletivo
    if aliquota.ad_rem is not None and seletivo is not None:
        if seletivo.quantidade is None:
            raise ErroRR("REG-023", "A quantidade do Imposto Seletivo deve ser informada para alíquota ad rem")
        if seletivo.quantidade <= 0:
            raise ErroRR("REG-023", "A quantidade do Imposto Seletivo deve ser maior do que zero para alíquota ad "
                                    "rem")
        if seletivo.unidade is None:
            raise ErroRR("REG-023", "A unidade de medida do Imposto Seletivo deve ser informada para alíquota ad "
                                    "rem")
    if item.quantidade is not None and item.quantidade <= 0:
        raise ErroRR("REG-023", "A quantidade do item deve ser maior do que zero")


def _is_nao_informado(ncm, nbs, data):
    tipo, codigo = _nomenclatura(ncm, nbs)
    return ErroRR("REG-013", f"Dados do Imposto Seletivo devem ser informados para {tipo} {codigo} e data "
                             f"{data.isoformat()}")


def _obter_tratamento_classificacao(base, item, data, tipo_dfe) -> dict:
    """ProcessamentoItemService.obterTratamentoClassificacao (L142 a L285).

    Devolve os campos do TratamentoClassificacaoModel.
    """
    ncm, nbs = item.ncm, item.nbs
    tratamento_desoneracao = None
    classificacao_is = None
    tratamento_is = None
    tem_desoneracao = False

    classificacao = _buscar_classificacao(base, item.c_class_trib, rr_base.TRIBUTO_CBS, data, CBS_E_IBS)
    _validar_classificacao_cbs_ibs(classificacao, item.cst, ncm, nbs)
    _validar_vinculo_tipo_dfe(base, tipo_dfe, classificacao, data)
    tratamento = _buscar_tratamento(base, classificacao["id"], data)
    cst, c_class_trib = item.cst, item.c_class_trib

    regular = item.tributacao_regular
    if tratamento["inExigeGrupoDesoneracao"]:
        if regular is None:
            raise ErroRR("REG-009", f"Dados da tributação regular devem ser informados para cClassTrib "
                                    f"{_texto(c_class_trib)} e CST {_texto(cst)}")
        cst, c_class_trib = regular.cst, regular.c_class_trib
        tratamento_desoneracao = tratamento
        classificacao = _buscar_classificacao(base, c_class_trib, rr_base.TRIBUTO_CBS, data, CBS_E_IBS)
        _validar_classificacao_cbs_ibs(classificacao, cst, ncm, nbs)
        _validar_vinculo_tipo_dfe(base, tipo_dfe, classificacao, data)
        tratamento = _buscar_tratamento(base, classificacao["id"], data)
        if tratamento["inIncompativelComSuspensao"]:
            raise ErroRR("REG-010", f"Classificação tributária (cClassTrib) {_texto(c_class_trib)} e situação "
                                    f"tributária (CST) {_texto(cst)} incompatíveis com suspensão")
        tem_desoneracao = True
    elif regular is not None and tipo_dfe is not None:
        # o grupo não entra no cálculo, mas o vínculo da cClassTrib dele com o tpDoc é validado
        _validar_vinculo_tipo_dfe(base, tipo_dfe, _buscar_classificacao(
            base, regular.c_class_trib, rr_base.TRIBUTO_CBS, data, CBS_E_IBS), data)

    if tipo_dfe is not None:
        _validar_nomenclatura_tp_doc(base, tipo_dfe["sigla"], classificacao, ncm, nbs, data)
    else:
        id_cl, codigo = classificacao["id"], classificacao["codigo"]
        if _vazio(ncm) and _vazio(nbs):
            _validar_ncm_aplicavel(base, ncm, id_cl, codigo, data, CBS_E_IBS)
            _validar_nbs_aplicavel(base, nbs, id_cl, codigo, data, CBS_E_IBS)
        if not _vazio(ncm) and _vazio(nbs):
            _validar_ncm_aplicavel(base, ncm, id_cl, codigo, data, CBS_E_IBS)
        if not _vazio(nbs) and _vazio(ncm):
            _validar_nbs_aplicavel(base, nbs, id_cl, codigo, data, CBS_E_IBS)

    seletivo = item.imposto_seletivo
    if seletivo is not None:
        _validar_cst(base, seletivo.cst, rr_base.TRIBUTO_IS, data)
        classificacao_is = _buscar_classificacao(base, seletivo.c_class_trib, rr_base.TRIBUTO_IS, data,
                                                 IMPOSTO_SELETIVO)
        _validar_classificacao_is(classificacao_is, seletivo.cst)

    aliquota_is = _analisar_aliquota_is(base, item, data)

    if tipo_dfe is not None and not rr_base.imposto_seletivo_aplicavel(tipo_dfe["sigla"]) and aliquota_is is not None:
        tipo, codigo = _nomenclatura(ncm, nbs)
        raise ErroRR("REG-032", f"O tipo de documento fiscal (tpDoc) {tipo_dfe['tipo']} não admite Imposto "
                                f"Seletivo, exigido para {tipo} de código {codigo}")

    if (aliquota_is is None and data.year >= ANO_LIMITE_SUPERIOR_ALIQUOTAS_NOMINAIS
            and _existe_incidencia_is(base, ncm, nbs, data)):
        if seletivo is None:
            raise _is_nao_informado(ncm, nbs, data)
        tipo, codigo = _nomenclatura(ncm, nbs)
        raise ErroRR("REG-035", f"A alíquota do Imposto Seletivo deve ser informada em "
                                f"aliquotasNominais.impostoSeletivo para {tipo} {codigo} e data {data.isoformat()}, "
                                "pois ainda não foi definida em lei")

    if aliquota_is is not None:
        if seletivo is None:
            raise _is_nao_informado(ncm, nbs, data)
        _validar_quantidade_e_unidade(item, aliquota_is)
        tratamento_is = _buscar_tratamento(base, classificacao_is["id"], data)
        # validarNomenclaturaAplicavelImpostoSeletivo: desativada no Java (corpo comentado)

    return {"tratamento_cbs_ibs": tratamento, "tratamento_is": tratamento_is,
            "tratamento_cbs_ibs_desoneracao": tratamento_desoneracao, "aliquota_is": aliquota_is,
            "tem_desoneracao": tem_desoneracao}


def processar_item(item, op, tipo_dfe, base, data: date) -> ItemProcessado:
    """ProcessamentoItemService.processarItem (L89 a L140), até antes de CalculoTributoService.calcular.

    `item` é o rr_entrada.Item (alterado no lugar, como no Java), `op` a rr_entrada.Operacao com a UF já
    resolvida, `tipo_dfe` o TipoDfe de rr_base.buscar_por_tipo (ou None sem tpDoc) e `data` o fato gerador
    aplicável. Levanta ErroRR na primeira verificação que falha.
    """
    if item.quantidade is None:
        item.quantidade = Decimal(1)
    if item.unidade is None:
        item.unidade = "UN"
    _ajustar_ncm(item, tipo_dfe)

    componentes = item.g_componentes_bc
    if componentes is not None and componentes.possui_componentes():
        _validar_base_calculo_com_componentes(op, item, componentes, data)
    if componentes is None or not componentes.possui_componentes():
        _validar_base_calculo_legado(item)

    _validar_ncm_nbs(base, item.ncm, item.nbs, data)
    _validar_cst(base, item.cst, rr_base.TRIBUTO_CBS, data)
    _validar_aliquotas_nominais(base, item, data)
    tratamento = _obter_tratamento_classificacao(base, item, data, tipo_dfe)

    codigo_uf = base.consultar_por_sigla(op.uf)  # UfService.buscar
    if codigo_uf is None:
        raise ErroRR("REG-020", f"UF de sigla {op.uf} não encontrada")
    return ItemProcessado(item=item, data=data, codigo_municipio=op.municipio, codigo_uf=codigo_uf, ncm=item.ncm,
                          nbs=item.nbs, tp_ente_gov=None, p_redutor=Decimal(0), **tratamento)
