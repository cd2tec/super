"""Motor do cálculo da skill cbs-ibs-simples: a operação e os itens.

Port do endpoint não assistido da calculadora-rtc-simples-nacional (tag 1.0.2): CalculoSimplesUnificadoService
e SimplesNacionalNaoAssistidoService (a linha de alíquotas vem no corpo), ProcessadorItemSimples,
EscolhaParAliquotasService, SimplesNacionalCalculoService, ItemCollectorSimples, ValidacaoNomenclaturaAdapter,
os modelos de saída e os serializers dos números. Também traz o catálogo de erros VSN-
(CodigoValidacaoSimples) e o tratamento de erro da API (shared/exceptionhandler).

Como a oficial, o motor não deduz, não corrige e não completa a entrada. Erro de validação sai na ordem
em que o Java o produz. Mensagens, títulos e detalhes são os do Java, letra por letra; onde o Java monta
a mensagem com `String.format`, o formato é o mesmo, com `%s` e `%d` trocados pelo valor. Só biblioteca
padrão, Decimal para todo número.

Fluxo de `calcular`, com o que cada passo guarda no `ContextoOperacao` que os itens recebem:
  1. `_ler_corpo`: o JSON vira `DadosOperacao` (texto, como o Jackson entrega). Estrutura fora do
     contrato é HTTP 400 (HttpMessageNotReadableException).
  2. validação global (HTTP 422): campos globais, documento, regime, quantidade de itens.
  3. regime de apuração, fator de transição de compra governamental, linha de alíquotas, avisos.
  4. `_processar_itens(contexto)`: cada item, em sequência e na ordem de entrada (o Java usa threads),
     e a resposta 200: `oper`, `resolucaoAliquotas`, `objetos` e `total`. Erro de item fica no próprio
     item (estado e lista `erros`), nunca derruba a operação.
"""
from __future__ import annotations

import decimal
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

_AQUI = Path(__file__).resolve().parent
if str(_AQUI) not in sys.path:
    sys.path.append(str(_AQUI))

import numeros  # noqa: E402  (precisa do caminho acima)
import simples_base  # noqa: E402

CAMINHO_NAO_ASSISTIDO = "/api/calculadora/observabilidade/simples-nacional/nao-assistido"  # com o /api do perfil offline
CAMINHO_ERROS = "/calculadora/observabilidade/simples-nacional/erros"
CAMINHO_ERROS_COM_API = "/api" + CAMINHO_ERROS

# --- Catálogo VSN- (CodigoValidacaoSimples) --------------------------------------------------------
# Um item por constante do enum, na ordem do Java; o comentário de cada item é o nome da constante.
# VSN-008 e VSN-009 foram aposentados e não existem. Os nomes abaixo são os das constantes Java.

BASE_AUSENTE = "VSN-001"
BASE_INVALIDA = "VSN-002"
IS_INVALIDO = "VSN-003"
ALIQUOTA_CBS_AUSENTE = "VSN-004"
ALIQUOTA_CBS_INVALIDA = "VSN-005"
ALIQUOTA_IBS_AUSENTE = "VSN-006"
ALIQUOTA_IBS_INVALIDA = "VSN-007"
NUMERO_AUSENTE = "VSN-010"
ITENS_VAZIOS = "VSN-011"
DATA_INVALIDA = "VSN-012"
DADO_GLOBAL_AUSENTE = "VSN-013"
TPDOC_AUSENTE = "VSN-014"
TPDOC_NAO_SUPORTADO = "VSN-015"
CRT_INVALIDO = "VSN-016"
TPREGIMEIBSCBS_INVALIDO = "VSN-017"
TPREGIMEIBSCBS_INCOMPATIVEL = "VSN-018"
OPERACAO_FORA_ESCOPO = "VSN-019"
CCLASSTRIB_AUSENTE = "VSN-020"
CCLASSTRIB_DESCONHECIDO = "VSN-021"
CCLASSTRIB_INCOMPATIVEL_TPDOC = "VSN-022"
CATIVSN_INCOMPATIVEL_CCLASSTRIB = "VSN-023"
TPENTEGOV_INVALIDO = "VSN-024"
COMPRA_GOV_SEM_VIGENCIA = "VSN-025"
PARAMETRIZACAO_INDISPONIVEL = "VSN-026"
CCLASSTRIB_NAO_IMPLEMENTADO = "VSN-027"
NI_CONTRIBUINTE_INVALIDO = "VSN-028"
ALIQUOTAS_EFETIVAS_INDISPONIVEIS = "VSN-029"
OPSIMPNAC_INVALIDO = "VSN-030"
REGAPIBSCBSSN_INVALIDO = "VSN-031"
REGAPTRIBSN_INVALIDO = "VSN-032"
CAMPO_REGIME_PROIBIDO = "VSN-033"
QUANTIDADE_ITENS_INVALIDA = "VSN-034"
IS_NAO_ADMITIDO = "VSN-035"
TRIBISSQN_INVALIDO = "VSN-036"
TPRETISSQN_INVALIDO = "VSN-037"
TPBM_INVALIDO = "VSN-038"
ALIQUOTAS_EFETIVAS_AUSENTES = "VSN-039"
FATOR_R_AUSENTE = "VSN-040"
NCM_NBS_SIMULTANEOS = "VSN-041"
NCM_INCOMPLETO = "VSN-042"
NBS_INCOMPLETO = "VSN-043"
NOMENCLATURA_OBRIGATORIA = "VSN-044"
NOMENCLATURA_INCOMPATIVEL = "VSN-045"
NOMENCLATURA_NAO_PERMITIDA = "VSN-046"
NCM_NAO_APLICAVEL = "VSN-047"
NBS_NAO_APLICAVEL = "VSN-048"
NCM_NAO_ENCONTRADO = "VSN-049"
NBS_NAO_ENCONTRADO = "VSN-050"
NOMENCLATURA_FORMATO_INVALIDO = "VSN-051"
DESCONHECIDO = "VSN-000"
FALHA_TECNICA = "VSN-999"


CODIGOS = {
    # BASE_AUSENTE
    "VSN-001": {
        "codigo": "VSN-001",
        "titulo": "Base de cálculo do Simples ausente",
        "descricao": "A base de cálculo do Simples Nacional é obrigatória e não foi informada.",
    },
    # BASE_INVALIDA
    "VSN-002": {
        "codigo": "VSN-002",
        "titulo": "Base de cálculo do Simples inválida (negativa/fora do formato)",
        "descricao": (
            "A base de cálculo informada é negativa ou não está no formato 13v2 (até 13 dígitos inteiros e até "
            "2 casas decimais, sem notação científica)."),
    },
    # IS_INVALIDO
    "VSN-003": {
        "codigo": "VSN-003",
        "titulo": "Valor do Imposto Seletivo inválido (negativo/fora do formato)",
        "descricao": (
            "O valor do Imposto Seletivo (vIS) informado é negativo ou não está no formato 13v2 (até 13 dígitos "
            "inteiros e até 2 casas decimais, sem notação científica)."),
    },
    # ALIQUOTA_CBS_AUSENTE
    "VSN-004": {
        "codigo": "VSN-004",
        "titulo": "Alíquota pCBSSN ausente",
        "descricao": "A alíquota efetiva de CBS do Simples Nacional (pCBSSN) é exigida para o cálculo do item e não foi informada.",
    },
    # ALIQUOTA_CBS_INVALIDA
    "VSN-005": {
        "codigo": "VSN-005",
        "titulo": "Alíquota pCBSSN inválida (negativa/fora do formato)",
        "descricao": (
            "A alíquota efetiva de CBS do Simples Nacional (pCBSSN) informada é negativa ou não está no formato "
            "aceito (até 3 dígitos inteiros e até 34 casas decimais, sem notação científica)."),
    },
    # ALIQUOTA_IBS_AUSENTE
    "VSN-006": {
        "codigo": "VSN-006",
        "titulo": "Alíquota pIBSSN ausente",
        "descricao": "A alíquota efetiva de IBS do Simples Nacional (pIBSSN) é exigida para o cálculo do item e não foi informada.",
    },
    # ALIQUOTA_IBS_INVALIDA
    "VSN-007": {
        "codigo": "VSN-007",
        "titulo": "Alíquota pIBSSN inválida (negativa/fora do formato)",
        "descricao": (
            "A alíquota efetiva de IBS do Simples Nacional (pIBSSN) informada é negativa ou não está no formato "
            "aceito (até 3 dígitos inteiros e até 34 casas decimais, sem notação científica)."),
    },
    # NUMERO_AUSENTE
    "VSN-010": {
        "codigo": "VSN-010",
        "titulo": "Número do item ausente",
        "descricao": "O número (identificador) do item é obrigatório e não foi informado.",
    },
    # ITENS_VAZIOS
    "VSN-011": {
        "codigo": "VSN-011",
        "titulo": "Lista de itens vazia",
        "descricao": "A lista de itens da operação é obrigatória e não pode estar vazia.",
    },
    # DATA_INVALIDA
    "VSN-012": {
        "codigo": "VSN-012",
        "titulo": "Data do fato gerador ausente/inválida",
        "descricao": "A data do fato gerador é obrigatória e deve estar no formato de data (YYYY-MM-DD).",
    },
    # DADO_GLOBAL_AUSENTE
    "VSN-013": {
        "codigo": "VSN-013",
        "titulo": "Dado global obrigatório ausente",
        "descricao": "Um campo global obrigatório da operação (id ou versao) não foi informado.",
    },
    # TPDOC_AUSENTE
    "VSN-014": {
        "codigo": "VSN-014",
        "titulo": "Tipo de documento fiscal (tpDoc) ausente",
        "descricao": "O tipo de documento fiscal (tpDoc) é obrigatório e não foi informado.",
    },
    # TPDOC_NAO_SUPORTADO
    "VSN-015": {
        "codigo": "VSN-015",
        "titulo": "Tipo de documento fiscal (tpDoc) não suportado",
        "descricao": (
            "O tipo de documento fiscal (tpDoc) informado não está habilitado para o cálculo do Simples "
            "Nacional na data do fato gerador. A habilitação de documentos é parametrizada em banco "
            "de dados (tabela de tipos de DFe do cálculo do Simples), conforme as NTs publicadas."),
    },
    # CRT_INVALIDO
    "VSN-016": {
        "codigo": "VSN-016",
        "titulo": "Código de Regime Tributário (CRT) ausente/inválido",
        "descricao": "O Código de Regime Tributário (CRT) é obrigatório e deve ser 1, 2, 3 ou 4.",
    },
    # TPREGIMEIBSCBS_INVALIDO
    "VSN-017": {
        "codigo": "VSN-017",
        "titulo": "Tipo de regime do IBS/CBS (tpRegimeIBSCBS) inválido",
        "descricao": "O tipo de regime de apuração do IBS/CBS (tpRegimeIBSCBS), quando informado, deve ser 1 ou 2.",
    },
    # TPREGIMEIBSCBS_INCOMPATIVEL
    "VSN-018": {
        "codigo": "VSN-018",
        "titulo": "tpRegimeIBSCBS incompatível com o CRT",
        "descricao": (
            "A combinação de CRT e tpRegimeIBSCBS informada é incoerente (regras C21a-10/20/30 da NT 2026.005): "
            "CRT 1, 2 ou 4 não admite tpRegimeIBSCBS=2; CRT 3 não admite tpRegimeIBSCBS=1; "
            "CRT 4 não admite tpRegimeIBSCBS algum."),
    },
    # OPERACAO_FORA_ESCOPO
    "VSN-019": {
        "codigo": "VSN-019",
        "titulo": "Operação fora do escopo do cálculo do Simples Nacional",
        "descricao": (
            "A operação não é calculada por este endpoint: optante pelo regime regular do IBS/CBS "
            "(tpRegimeIBSCBS=1), MEI (CRT=4) ou regime normal puro (CRT=3 sem tpRegimeIBSCBS=2)."),
    },
    # CCLASSTRIB_AUSENTE
    "VSN-020": {
        "codigo": "VSN-020",
        "titulo": "Código de Classificação Tributária (cClassTrib) ausente",
        "descricao": "O código de classificação tributária do IBS/CBS (cClassTrib) é obrigatório e não foi informado.",
    },
    # CCLASSTRIB_DESCONHECIDO
    "VSN-021": {
        "codigo": "VSN-021",
        "titulo": "cClassTrib desconhecido ou fora de vigência",
        "descricao": (
            "O cClassTrib informado não existe ou não está vigente na data do fato gerador: "
            "o Tipo de Receita Bruta do Simples Nacional (tpRBSN) não pôde ser determinado."),
    },
    # CCLASSTRIB_INCOMPATIVEL_TPDOC
    "VSN-022": {
        "codigo": "VSN-022",
        "titulo": "cClassTrib incompatível com o tpDoc",
        "descricao": "O cClassTrib informado não é admissível para o tipo de documento fiscal (tpDoc) da operação.",
    },
    # CATIVSN_INCOMPATIVEL_CCLASSTRIB
    "VSN-023": {
        "codigo": "VSN-023",
        "titulo": "Código de Atividade do Simples Nacional incompatível com cClassTrib",
        "descricao": (
            "O código de atividade cAtivSN='90' (Operações não Tributáveis) é incompatível com cClassTrib "
            "cujo tpRBSN esteja entre 1 e 5 (regra UB14-90 da NT 2026.005)."),
    },
    # TPENTEGOV_INVALIDO
    "VSN-024": {
        "codigo": "VSN-024",
        "titulo": "Tipo de ente governamental (tpEnteGov) inválido",
        "descricao": (
            "O tipo de ente governamental (tpEnteGov), quando informado, deve ser 1 (União), 2 (Estado), "
            "3 (Distrito Federal) ou 4 (Município). O campo é da operação \u2014 espelha a tag "
            "gCompraGov/tpEnteGov da identificação da nota (grupo B/ide do leiaute do DF-e) \u2014 e "
            "sua invalidade rejeita a operação inteira."),
    },
    # COMPRA_GOV_SEM_VIGENCIA
    "VSN-025": {
        "codigo": "VSN-025",
        "titulo": "Parametrização de compra governamental indisponível para a data",
        "descricao": (
            "A data do fato gerador está fora de todas as vigências do parâmetro de transição de alíquotas "
            "de compra governamental do Simples Nacional (NT 2026.005, seção 5.3.3; arts. 372 e 473 "
            "da LC 214/2025). Como o tpEnteGov é dado da operação, a falha rejeita a operação "
            "inteira."),
    },
    # PARAMETRIZACAO_INDISPONIVEL
    "VSN-026": {
        "codigo": "VSN-026",
        "titulo": "Parametrização do cálculo do Simples Nacional indisponível para a data",
        "descricao": (
            "Não há linha vigente, na data do fato gerador, em uma tabela de parametrização do cálculo do "
            "Simples Nacional (efeito do tipo de receita bruta ou matriz de regime de apuração). "
            "O cálculo nunca assume comportamento implícito: a parametrização deve ser corrigida "
            "por script de migração."),
    },
    # CCLASSTRIB_NAO_IMPLEMENTADO
    "VSN-027": {
        "codigo": "VSN-027",
        "titulo": "cClassTrib sem tratamento implementado no cálculo do Simples Nacional",
        "descricao": (
            "A classificação tributária (cClassTrib) informada está vinculada, na data do fato gerador, a "
            "tratamento tributário marcado como ainda não implementado na Calculadora (indicador de "
            "ajuste do tratamento tributário). O item é reportado com o estado NAO_IMPLEMENTADO, "
            "sem cálculo e fora dos totais; a funcionalidade está prevista no roadmap e será "
            "disponibilizada em versão futura."),
    },
    # NI_CONTRIBUINTE_INVALIDO
    "VSN-028": {
        "codigo": "VSN-028",
        "titulo": "Identificação do contribuinte (niContribuinte) ausente ou inválida",
        "descricao": (
            "A identificação do contribuinte emitente (niContribuinte) é obrigatória no cálculo assistido e "
            "deve ser um CNPJ alfanumérico válido: 14 caracteres (12 caracteres [A-Z0-9] e 2 dígitos "
            "verificadores), aceito com ou sem máscara. É por ela e pelo período de apuração da data "
            "do fato gerador que as alíquotas efetivas são obtidas na Tabela de Alíquotas Efetivas do "
            "Simples Nacional (NT 2026.005, seção 5.3.3). A falha rejeita a operação inteira."),
    },
    # ALIQUOTAS_EFETIVAS_INDISPONIVEIS
    "VSN-029": {
        "codigo": "VSN-029",
        "titulo": "Alíquotas efetivas do Simples Nacional indisponíveis para o contribuinte no período de apuração",
        "descricao": (
            "Não há registro na Tabela de Alíquotas Efetivas do Simples Nacional para o contribuinte "
            "(niContribuinte) no período de apuração (AAAAMM) derivado da data do fato gerador. Sem as "
            "alíquotas efetivas o cálculo assistido não pode ser realizado; a operação inteira é "
            "rejeitada. Alternativa: o endpoint clássico, informando as alíquotas por item."),
    },
    # OPSIMPNAC_INVALIDO
    "VSN-030": {
        "codigo": "VSN-030",
        "titulo": "Situação perante o Simples Nacional (opSimpNac) ausente ou inválida",
        "descricao": (
            "Na NFS-e, a situação do prestador perante o Simples Nacional (opSimpNac, DPS prest/regTrib) é "
            "obrigatória e deve ser 1 (não optante), 2 (MEI), 3 (ME/EPP) ou 4 (opção pendente)."),
    },
    # REGAPIBSCBSSN_INVALIDO
    "VSN-031": {
        "codigo": "VSN-031",
        "titulo": "Regime de apuração do IBS e da CBS no Simples (regApIBSCBSSN) incoerente ou inválido",
        "descricao": (
            "O regApIBSCBSSN (DPS prest/regTrib) deve ser 1 (IBS e CBS pelo Simples), 2 (CBS pelo Simples e IBS "
            "pelo regime regular) ou 3 (os dois pelo regime regular). É obrigatório com opSimpNac 3 ou "
            "4 (regra 313 da NFS-e) e não pode ser informado com opSimpNac 1 ou 2."),
    },
    # REGAPTRIBSN_INVALIDO
    "VSN-032": {
        "codigo": "VSN-032",
        "titulo": "Regime de apuração dos tributos no Simples (regApTribSN) incoerente ou inválido",
        "descricao": (
            "O regApTribSN (DPS prest/regTrib) deve ser 1 (tributos federais e ISS pelo Simples), 2 (federais "
            "pelo Simples e ISS fora dele) ou 3 (federais e ISS fora). É obrigatório com opSimpNac 3 ou 4 "
            "(regra E0166, estendida pela NT 009) e não pode ser informado com opSimpNac 1 ou 2 (E0162)."),
    },
    # CAMPO_REGIME_PROIBIDO
    "VSN-033": {
        "codigo": "VSN-033",
        "titulo": "Campo de regime não pertence ao documento",
        "descricao": (
            "O regime do emitente é informado no vocabulário do próprio documento: CRT e tpRegimeIBSCBS nos "
            "documentos com CRT (NF-e, NFC-e, CT-e, CT-e OS, BP-e, BP-e TM e NFCom); opSimpNac, "
            "regApIBSCBSSN e regApTribSN na NFS-e. Campo do outro vocabulário não pode ser informado."),
    },
    # QUANTIDADE_ITENS_INVALIDA
    "VSN-034": {
        "codigo": "VSN-034",
        "titulo": "Número de itens não admitido pelo documento",
        "descricao": (
            "O documento admite um número máximo de itens (parametrizado em banco). A NFS-e tem exatamente 1 "
            "item: a DPS tem um só serviço, com um cTribNac e um cClassTrib."),
    },
    # IS_NAO_ADMITIDO
    "VSN-035": {
        "codigo": "VSN-035",
        "titulo": "Imposto Seletivo não admitido pelo documento",
        "descricao": "O documento não tem Imposto Seletivo (NFS-e): o valorImpostoSeletivo deve estar ausente ou ser zero.",
    },
    # TRIBISSQN_INVALIDO
    "VSN-036": {
        "codigo": "VSN-036",
        "titulo": "Tributação do ISSQN (tribISSQN) ausente ou inválida",
        "descricao": (
            "Na NFS-e, a tributação do ISSQN (tribISSQN) é obrigatória quando a atividade tem ISS no Simples e o "
            "regime calcula tributos, e deve ser 1 (operação tributável), 2 (imunidade), 3 (exportação "
            "de serviço) ou 4 (não incidência)."),
    },
    # TPRETISSQN_INVALIDO
    "VSN-037": {
        "codigo": "VSN-037",
        "titulo": "Retenção do ISSQN (tpRetISSQN) ausente ou inválida",
        "descricao": (
            "Na NFS-e, o tipo de retenção do ISSQN (tpRetISSQN) é obrigatório quando tribISSQN = 1, nas atividades "
            "com ISS no Simples, e deve ser 1 (não retido), 2 (retido pelo tomador) ou 3 (retido pelo "
            "intermediário)."),
    },
    # TPBM_INVALIDO
    "VSN-038": {
        "codigo": "VSN-038",
        "titulo": "Tipo de benefício municipal (tpBM) inválido",
        "descricao": (
            "Na NFS-e, o tipo de benefício municipal do ISS (tpBM), quando informado, deve ser 1 (isenção), 2 "
            "(redução da base em %), 3 (redução da base em R$) ou 4 (alíquota diferenciada). Ausente = "
            "sem benefício municipal."),
    },
    # ALIQUOTAS_EFETIVAS_AUSENTES
    "VSN-039": {
        "codigo": "VSN-039",
        "titulo": "Alíquotas efetivas do contribuinte (aliquotasEfetivas) ausentes",
        "descricao": (
            "No cálculo não assistido, a linha de alíquotas efetivas do contribuinte no período "
            "(aliquotasEfetivas) é obrigatória, exceto quando o regime não calcula tributos "
            "(SOMENTE_RECEITA). A falha rejeita a operação inteira."),
    },
    # FATOR_R_AUSENTE
    "VSN-040": {
        "codigo": "VSN-040",
        "titulo": "Fator \"r\" (fatorR) ausente",
        "descricao": (
            "O fatorR da linha de alíquotas é exigido quando o item tem atividade sujeita ao fator \"r\" (a 09): "
            "é ele que decide entre o Anexo III (fator r igual ou superior a 28%) e o Anexo V."),
    },
    # NCM_NBS_SIMULTANEOS
    "VSN-041": {
        "codigo": "VSN-041",
        "titulo": "NCM e NBS simultâneos",
        "descricao": "Informe NCM ou NBS, nunca ambos.",
    },
    # NCM_INCOMPLETO
    "VSN-042": {
        "codigo": "VSN-042",
        "titulo": "NCM incompleto",
        "descricao": "A classificação tributária exige um NCM completo com 8 caracteres.",
    },
    # NBS_INCOMPLETO
    "VSN-043": {
        "codigo": "VSN-043",
        "titulo": "NBS incompleto",
        "descricao": "A classificação tributária exige um NBS completo com 9 caracteres.",
    },
    # NOMENCLATURA_OBRIGATORIA
    "VSN-044": {
        "codigo": "VSN-044",
        "titulo": "Nomenclatura obrigatória",
        "descricao": "Informe NCM ou NBS completo conforme exigido pela classificação tributária.",
    },
    # NOMENCLATURA_INCOMPATIVEL
    "VSN-045": {
        "codigo": "VSN-045",
        "titulo": "Nomenclatura incompatível com o documento",
        "descricao": "A nomenclatura informada é incompatível com o tipo de documento fiscal ou a classificação tributária.",
    },
    # NOMENCLATURA_NAO_PERMITIDA
    "VSN-046": {
        "codigo": "VSN-046",
        "titulo": "Nomenclatura não permitida",
        "descricao": "A nomenclatura informada não é permitida para o tipo de documento fiscal.",
    },
    # NCM_NAO_APLICAVEL
    "VSN-047": {
        "codigo": "VSN-047",
        "titulo": "NCM não aplicável",
        "descricao": "O NCM informado não está vinculado à classificação tributária.",
    },
    # NBS_NAO_APLICAVEL
    "VSN-048": {
        "codigo": "VSN-048",
        "titulo": "NBS não aplicável",
        "descricao": "O NBS informado não está vinculado à classificação tributária.",
    },
    # NCM_NAO_ENCONTRADO
    "VSN-049": {
        "codigo": "VSN-049",
        "titulo": "NCM desconhecido",
        "descricao": "O NCM informado não existe ou não está vigente na data.",
    },
    # NBS_NAO_ENCONTRADO
    "VSN-050": {
        "codigo": "VSN-050",
        "titulo": "NBS desconhecido",
        "descricao": "O NBS informado não existe ou não está vigente na data.",
    },
    # NOMENCLATURA_FORMATO_INVALIDO
    "VSN-051": {
        "codigo": "VSN-051",
        "titulo": "Formato de nomenclatura inválido",
        "descricao": "NCM deve conter 8 ou NBS deve conter 9 dígitos ASCII, sem espaços ou símbolos.",
    },
    # DESCONHECIDO
    "VSN-000": {
        "codigo": "VSN-000",
        "titulo": "Erro de validação não categorizado",
        "descricao": "O tipo de validação aplicado não possui uma categoria específica.",
    },
    # FALHA_TECNICA
    "VSN-999": {
        "codigo": "VSN-999",
        "titulo": "Falha técnica no cálculo do item",
        "descricao": "Ocorreu uma falha técnica inesperada durante o cálculo do item.",
    },
}


def descrever_erro(codigo):
    """SimplesNacionalController.buscarErro + SimplesNacionalErroService.buscarPorCodigo.

    Devolve (200, {codigo, titulo, descricao}) ou (404, None) para código fora do catálogo (inclui
    VSN-008 e VSN-009, aposentados). A consulta é exata: "vsn-001" e " VSN-001" não existem.
    """
    if codigo is None:
        return 404, None
    entrada = CODIGOS.get(codigo)
    if entrada is None:
        return 404, None
    return 200, {"codigo": codigo, "titulo": entrada["titulo"], "descricao": entrada["descricao"]}


def consultar_erro(codigo, *, instancia=None, base_url=None, agora=None):
    """SimplesNacionalController.buscarErro com o parâmetro `codigo` como a requisição o traz.

    Sem o parâmetro (None) a oficial responde 400 de parâmetro obrigatório ausente; com ele, é `descrever_erro`.
    """
    if codigo is None:
        instancia = CAMINHO_ERROS_COM_API if instancia is None else instancia
        return simples_base.Base._corpo_erro(simples_base._ErroParametroAusente("codigo", "String"),
                                             instancia, base_url, agora)
    return descrever_erro(codigo)


# --- Texto como o Java vê ---------------------------------------------------------------------

_BRANCOS_CONTROLE = frozenset("\t\n\x0b\x0c\r\x1c\x1d\x1e\x1f")
_NAO_BRANCOS_UNICODE = frozenset("   ")  # espaços que Character.isWhitespace recusa
_ATE_ESPACO = "".join(chr(i) for i in range(0x21))  # String.trim tira tudo até U+0020
_INTEIRO = re.compile(r"[+-]?\d+")


def _e_branco(c):
    # Character.isWhitespace
    if c in _BRANCOS_CONTROLE:
        return True
    return c not in _NAO_BRANCOS_UNICODE and unicodedata.category(c) in ("Zs", "Zl", "Zp")


def _informado(valor):
    # EscolhaParAliquotasService.informado: não nulo e não String.isBlank
    return valor is not None and not all(_e_branco(c) for c in valor)


def _aparar(valor):
    # String.trim
    return valor.strip(_ATE_ESPACO)


def _inteiro(valor):
    # EscolhaParAliquotasService.inteiro: Integer.valueOf(valor.trim()), None quando inválido
    if not _informado(valor):
        return None
    texto = _aparar(valor)
    if not _INTEIRO.fullmatch(texto):
        return None
    try:
        numero = int(texto)
    except ValueError:
        return None
    return numero if -(2 ** 31) <= numero < 2 ** 31 else None


def _s(valor):
    # String.format("%s", String): o nulo sai como "null"
    return "null" if valor is None else valor


def _d(valor):
    # String.format("%d", Integer): o nulo sai como "null"
    return "null" if valor is None else str(valor)


# --- Erros ------------------------------------------------------------------------------------

def _erro_global(codigo, campo, detalhe, base_url):
    # ErroGlobalSimples.de; `campo` é o caminho a partir da raiz, sem "#/"
    return {
        "type": f"{base_url}{CAMINHO_ERROS}?codigo={codigo}",
        "title": CODIGOS[codigo]["titulo"],
        "code": codigo,
        "pointer": "#/" + campo,
        "detail": detalhe,
    }


class _ErrosCalculo(Exception):
    """ErrosCalculoException: lista de erros globais, que viram HTTP 422."""

    def __init__(self, erros):
        super().__init__("Erros de cálculo encontrados")
        self.erros = list(erros)


class _CorpoIlegivel(Exception):
    """HttpMessageNotReadableException: corpo que não cabe no contrato, HTTP 400."""

    def __init__(self, detalhe):
        super().__init__(detalhe)
        self.detalhe = detalhe


class _ErroSistema(Exception):
    """Exception não prevista no ApiExceptionHandler: HTTP 500."""

    def __init__(self, detalhe):
        super().__init__(detalhe)
        self.detalhe = detalhe


def _corpo_422(erros, instancia, agora):
    # ApiExceptionHandler.handleErrosCalculoException
    return 422, {
        "type": "about:blank",
        "title": "Erros no processamento do cálculo do Simples Nacional",
        "status": 422,
        "detail": f"O cálculo contém {len(erros)} erro(s).",
        "instance": instancia,
        "timestamp": simples_base.instante(agora),
        "errors": list(erros),
    }


def _corpo_problema(status, titulo, caminho, detalhe, base_url, instancia, agora):
    # ProblemType.getURI + createProblem: type, title, status, detail, instance, timestamp
    return status, {
        "type": f"{base_url}/errors/{caminho}",
        "title": titulo,
        "status": status,
        "detail": detalhe,
        "instance": instancia,
        "timestamp": simples_base.instante(agora),
    }


# --- Leitura do corpo (o que o Jackson faz com OperacaoSimplesNaoAssistidaInput) -----------------
# Campos de texto aceitam número e booleano (coerção de escalar); objeto e lista no lugar de texto, lista
# ou registro são HTTP 400, com a mensagem do Jackson. Campo desconhecido é ignorado. O que o JSON traz
# dentro de cada item de `itens` é lido por `_ler_item`, no mesmo estilo.

_PACOTE_ENTRADA = "br.gov.serpro.rtc.api.simplesnacional.model.input."
_T_OPERACAO = _PACOTE_ENTRADA + "OperacaoSimplesNaoAssistidaInput"
_T_ALIQUOTAS = _PACOTE_ENTRADA + "AliquotasEfetivasInput"
_T_ANEXO_PAR_UNICO = _T_ALIQUOTAS + "$AnexoParUnicoInput"
_T_ANEXO_COM_ISS = _T_ALIQUOTAS + "$AnexoComIssInput"
_T_PAR = _T_ALIQUOTAS + "$ParAliquotasInput"
_T_LISTA_ITENS = "java.util.ArrayList<" + _PACOTE_ENTRADA + "ItemSimplesUnificadoInput>"
_T_STRING = "java.lang.String"
_T_BOOLEAN = "java.lang.Boolean"

_TEXTO, _BOOLEANO, _LISTA = "texto", "booleano", "lista"

_CAMPOS = {
    _T_OPERACAO: {
        "id": _TEXTO, "versao": _TEXTO, "data": _TEXTO, "tpDoc": _TEXTO, "CRT": _TEXTO,
        "tpRegimeIBSCBS": _TEXTO, "opSimpNac": _TEXTO, "regApIBSCBSSN": _TEXTO, "regApTribSN": _TEXTO,
        "tpEnteGov": _TEXTO, "aliquotasEfetivas": _T_ALIQUOTAS, "itens": _LISTA,
    },
    _T_ALIQUOTAS: {
        "fatorR": _BOOLEANO, "impedimentoSublimite": _BOOLEANO, "anexoI": _T_ANEXO_PAR_UNICO,
        "anexoII": _T_ANEXO_PAR_UNICO, "anexoIII": _T_ANEXO_COM_ISS, "anexoIV": _T_ANEXO_COM_ISS,
        "anexoV": _T_ANEXO_COM_ISS,
    },
    _T_ANEXO_PAR_UNICO: {"pCBSSN": _TEXTO, "pIBSSN": _TEXTO},
    _T_ANEXO_COM_ISS: {"pCBSSN": _TEXTO, "pIBSSN": _TEXTO, "semRedistribuicaoISS": _T_PAR},
    _T_PAR: {"pCBSSN": _TEXTO, "pIBSSN": _TEXTO},
}


def _natureza(valor):
    # (rótulo do valor para "from X value (token ...)", token, rótulo do construtor)
    if isinstance(valor, dict):
        return "Object", "START_OBJECT"
    if isinstance(valor, list):
        return "Array", "START_ARRAY"
    if isinstance(valor, bool):
        return "Boolean", "VALUE_TRUE" if valor else "VALUE_FALSE"
    if isinstance(valor, int):
        return "Integer", "VALUE_NUMBER_INT"
    if isinstance(valor, Decimal):
        return "Floating-point", "VALUE_NUMBER_FLOAT"
    if isinstance(valor, str):
        return "String", "VALUE_STRING"
    raise TypeError(f"valor fora do JSON: {type(valor).__name__}")


def _de_valor(tipo, valor):
    rotulo, token = _natureza(valor)
    return f"Cannot deserialize value of type `{tipo}` from {rotulo} value (token `JsonToken.{token}`)"


_CONTEXTO_EXATO = decimal.Context(prec=1600, rounding=decimal.ROUND_HALF_EVEN)  # as potências de 2 cabem inteiras


def _para_double(numerador, denominador):
    """O double mais próximo de numerador/denominador (positivo), como (mantissa, expoente) ou None se estoura.

    Só aritmética de inteiros: o motor não usa ponto flutuante nem para dizer como o Java escreve um double.
    """
    k = numerador.bit_length() - denominador.bit_length()
    acima = numerador >= (denominador << k) if k >= 0 else (numerador << -k) >= denominador
    if not acima:
        k -= 1
    expoente = max(k - 52, -1074)
    divisor = denominador << expoente if expoente >= 0 else denominador
    dividendo = numerador if expoente >= 0 else numerador << -expoente
    mantissa, resto = divmod(dividendo, divisor)
    if 2 * resto > divisor or (2 * resto == divisor and mantissa % 2 == 1):
        mantissa += 1
    if mantissa == 1 << 53:
        mantissa, expoente = 1 << 52, expoente + 1
    if mantissa.bit_length() + expoente > 1024:
        return None
    return mantissa, expoente


def _razao(valor):
    sinal, digitos, expoente = valor.as_tuple()
    n = int("".join(str(d) for d in digitos))
    return (n * 10 ** expoente, 1) if expoente >= 0 else (n, 10 ** -expoente)


def _java_double(valor):
    # Double.toString do número do JSON (o Jackson o lê como double): o decimal mais curto, e o mais perto
    # do double, que volta ao mesmo double, com ao menos dois dígitos; fora de 10^-3 a 10^7 em notação científica
    if valor.is_zero():
        return "-0.0" if valor.is_signed() else "0.0"
    sinal = "-" if valor < 0 else ""
    alvo = _para_double(*_razao(abs(valor)))
    if alvo is None:
        return sinal + "Infinity"
    mantissa, expoente = alvo
    exato = (_CONTEXTO_EXATO.divide(Decimal(mantissa << expoente), Decimal(1)) if expoente >= 0
             else _CONTEXTO_EXATO.divide(Decimal(mantissa), Decimal(1 << -expoente)))
    for n in range(2, 18):
        candidato = decimal.Context(prec=n, rounding=decimal.ROUND_HALF_EVEN).plus(exato)
        if _para_double(*_razao(candidato)) == alvo:
            break
    partes = candidato.normalize().as_tuple()
    digitos = "".join(str(d) for d in partes.digits)
    expoente10 = partes.exponent
    cientifico = len(digitos) + expoente10 - 1
    if -3 <= cientifico < 7:
        if expoente10 >= 0:
            inteiro, fracao = digitos + "0" * expoente10, "0"
        elif len(digitos) + expoente10 > 0:
            inteiro, fracao = digitos[:len(digitos) + expoente10], digitos[len(digitos) + expoente10:]
        else:
            inteiro, fracao = "0", "0" * -(len(digitos) + expoente10) + digitos
        return f"{sinal}{inteiro}.{fracao}"
    return f"{sinal}{digitos[0]}.{digitos[1:] or '0'}E{cientifico}"


def _sem_construtor(tipo, valor):
    # registro recebendo escalar: o Jackson tenta um construtor de um argumento
    if isinstance(valor, str):
        como = (f"no String-argument constructor/factory method to deserialize from String value "
                f"('{_aparar(valor)}')")
    elif isinstance(valor, bool):
        como = ("no boolean/Boolean-argument constructor/factory method to deserialize from boolean value "
                f"({'true' if valor else 'false'})")
    elif isinstance(valor, int):
        if _INT_MIN <= valor <= _INT_MAX:
            como = f"no int/Int-argument constructor/factory method to deserialize from Number value ({valor})"
        elif _LONG_MIN <= valor <= _LONG_MAX:
            como = f"no long/Long-argument constructor/factory method to deserialize from Number value ({valor})"
        else:
            como = ("no BigInteger-argument constructor/factory method to deserialize from Number value "
                    f"({valor})")
    else:
        como = ("no double/Double-argument constructor/factory method to deserialize from Number value "
                f"({_java_double(valor)})")
    return f"Cannot construct instance of `{tipo}` (although at least one Creator exists): {como}"


def _texto_no_lugar_de_registro(tipo, texto):
    # BeanDeserializer.deserializeFromString: o texto é aparado; vazio é coerção recusada
    if _aparar(texto) == "":
        return (f'Cannot coerce empty String ("") to `{tipo}` value (but could if coercion was enabled using '
                "`CoercionConfig`)")
    return _sem_construtor(tipo, texto)


def _ler_texto(valor):
    # StringDeserializer: escalar vira texto (número com a forma escrita; ver o limite no relatório)
    if valor is None or isinstance(valor, str):
        return valor
    if isinstance(valor, bool):
        return "true" if valor else "false"
    if isinstance(valor, (int, Decimal)):
        return str(valor)
    raise _CorpoIlegivel("JSON parse error: " + _de_valor(_T_STRING, valor))


def _ler_booleano(valor):
    # BooleanDeserializer: true/false, texto "true"/"false" (sem caixa, aparado) e inteiro (0 falso)
    if valor is None or isinstance(valor, bool):
        return valor
    if isinstance(valor, int):
        return valor != 0
    if isinstance(valor, str):
        aparado = _aparar(valor)
        if aparado == "" or aparado == "null":
            return None  # texto vazio, em branco ou "null" vale nulo no Boolean
        if aparado.lower() in ("true", "false"):
            return aparado.lower() == "true"
        raise _CorpoIlegivel('JSON parse error: Cannot deserialize value of type `java.lang.Boolean` from '
                             f'String "{aparado}": only "true" or "false" recognized')
    raise _CorpoIlegivel("JSON parse error: " + _de_valor(_T_BOOLEAN, valor))


def _ler_registro(tipo, no):
    """Lê um registro do contrato: dict com todos os campos do tipo (None onde faltou)."""
    if no is None:
        return None
    if not isinstance(no, dict):
        if isinstance(no, list):
            raise _CorpoIlegivel("JSON parse error: " + _de_valor(tipo, no))
        if isinstance(no, str):
            raise _CorpoIlegivel("JSON parse error: " + _texto_no_lugar_de_registro(tipo, no))
        raise _CorpoIlegivel("JSON parse error: " + _sem_construtor(tipo, no))
    campos = _CAMPOS[tipo]
    lido = dict.fromkeys(campos)
    for nome, valor in no.items():  # na ordem do JSON, como o Jackson: o primeiro defeito vence
        natureza = campos.get(nome)
        if natureza is None:
            continue
        if natureza == _TEXTO:
            lido[nome] = _ler_texto(valor)
        elif natureza == _BOOLEANO:
            lido[nome] = _ler_booleano(valor)
        elif natureza == _LISTA:
            if valor is not None and not isinstance(valor, list):
                raise _CorpoIlegivel("JSON parse error: " + _de_valor(_T_LISTA_ITENS, valor))
            lido[nome] = _ler_itens(valor)
        else:
            lido[nome] = _ler_registro(natureza, valor)
    return lido


def _ler_itens(lista):
    # Lista de ItemSimplesUnificadoInput: cada elemento é um EntradaItem, ou None (elemento nulo)
    return None if lista is None else [_ler_item(elemento) for elemento in lista]


# --- Leitura de um item (ItemSimplesUnificadoInput) -------------------------------------------
# `numero` é Integer, como o Jackson o lê: inteiro, número com fração (truncado), texto aparado com um
# inteiro (vazio ou "null" é nulo); o resto é texto. `ncm` e `nbs` aceitam também `NCM` e `NBS`; com os
# dois nomes no JSON, vale o último. Elemento que não é objeto é HTTP 400.

_T_ITEM = _PACOTE_ENTRADA + "ItemSimplesUnificadoInput"
_T_INTEGER = "java.lang.Integer"
_INT_MIN, _INT_MAX = -(2 ** 31), 2 ** 31 - 1
_LONG_MIN, _LONG_MAX = -(2 ** 63), 2 ** 63 - 1
_FAIXA_INT = "(-2147483648 - 2147483647)"

_CAMPOS_ITEM = {  # nome no JSON -> campo do EntradaItem
    "numero": "numero", "cAtivSN": "c_ativ_sn", "cClassTrib": "c_class_trib", "nbs": "nbs", "NBS": "nbs",
    "ncm": "ncm", "NCM": "ncm", "baseCalculo": "base_calculo", "valorImpostoSeletivo": "valor_imposto_seletivo",
    "tribISSQN": "trib_issqn", "tpRetISSQN": "tp_ret_issqn", "tpBM": "tp_bm",
}


@dataclass(frozen=True)
class EntradaItem:
    """ItemSimplesUnificadoInput: o item como chegou (texto, e `numero` inteiro), None onde faltou."""
    numero: int | None = None
    c_ativ_sn: str | None = None
    c_class_trib: str | None = None
    nbs: str | None = None
    ncm: str | None = None
    base_calculo: str | None = None
    valor_imposto_seletivo: str | None = None
    trib_issqn: str | None = None
    tp_ret_issqn: str | None = None
    tp_bm: str | None = None


def _ler_item(no):
    if no is None:
        return None
    if not isinstance(no, dict):
        if isinstance(no, list):
            raise _CorpoIlegivel("JSON parse error: " + _de_valor(_T_ITEM, no))
        if isinstance(no, str):
            raise _CorpoIlegivel("JSON parse error: " + _texto_no_lugar_de_registro(_T_ITEM, no))
        raise _CorpoIlegivel("JSON parse error: " + _sem_construtor(_T_ITEM, no))
    lido = {}
    for nome, valor in no.items():  # na ordem do JSON: o primeiro defeito vence, o último valor fica
        campo = _CAMPOS_ITEM.get(nome)
        if campo is None:
            continue
        lido[campo] = _ler_inteiro(valor) if campo == "numero" else _ler_texto(valor)
    return EntradaItem(**lido)


def _ler_inteiro(valor):
    # NumberDeserializers.IntegerDeserializer
    if valor is None:
        return None
    if isinstance(valor, bool) or isinstance(valor, (dict, list)):
        raise _CorpoIlegivel("JSON parse error: " + _de_valor(_T_INTEGER, valor))
    if isinstance(valor, (int, Decimal)):
        if valor < _INT_MIN or valor > _INT_MAX:
            raise _CorpoIlegivel(f"JSON parse error: Numeric value ({valor}) out of range of `int` {_FAIXA_INT}")
        return int(valor)  # número com fração: truncado (ACCEPT_FLOAT_AS_INT)
    texto = _aparar(valor)
    if texto in ("", "null"):
        return None
    prefixo = f"JSON parse error: Cannot deserialize value of type `{_T_INTEGER}` from String \"{texto}\": "
    if len(texto) > 9:  # Long.parseLong, e então a faixa do int
        numero = _parse_inteiro_java(texto, _LONG_MIN, _LONG_MAX)
        if numero is not None and not _INT_MIN <= numero <= _INT_MAX:
            raise _CorpoIlegivel(prefixo + f"Overflow: numeric value ({texto}) out of range of "
                                 f"`{_T_INTEGER}` (-2147483648 -2147483647)")
    else:  # Integer.parseInt
        numero = _parse_inteiro_java(texto, _INT_MIN, _INT_MAX)
    if numero is None:
        raise _CorpoIlegivel(prefixo + f"not a valid `{_T_INTEGER}` value")
    return numero


def _parse_inteiro_java(texto, minimo, maximo):
    # Integer.parseInt / Long.parseLong: sinal opcional e dígitos Unicode (Character.digit); None se inválido
    sinal, digitos = (texto[0], texto[1:]) if texto[:1] in ("+", "-") else ("+", texto)
    if not digitos:
        return None
    numero = 0
    for c in digitos:
        valor = unicodedata.decimal(c, None)
        if valor is None:
            return None
        numero = numero * 10 + valor
    numero = -numero if sinal == "-" else numero
    return numero if minimo <= numero <= maximo else None


@dataclass(frozen=True)
class DadosOperacao:
    """CalculoSimplesUnificadoService.DadosOperacaoSimples: a operação como chegou (texto ou None).

    `itens` é a lista de EntradaItem (None onde o elemento veio nulo), ou None se ausente.
    `aliquotas_efetivas` é o registro lido (dict com fatorR, impedimentoSublimite, anexoI..anexoV) ou None.
    """
    id: str | None
    versao: str | None
    data: str | None
    tp_doc: str | None
    crt: str | None
    tp_regime_ibscbs: str | None
    op_simp_nac: str | None
    reg_ap_ibscbssn: str | None
    reg_ap_tribsn: str | None
    tp_ente_gov: str | None
    itens: list | None
    aliquotas_efetivas: dict | None


def _ler_corpo(operacao):
    if operacao is None:
        raise _CorpoIlegivel(
            "Required request body is missing: public org.springframework.http.ResponseEntity<br.gov.serpro."
            "rtc.api.simplesnacional.model.output.ObservabilidadeSimplesDomain> br.gov.serpro.rtc.api."
            "simplesnacional.controller.SimplesNacionalController.calcularTributosSimplesNacionalNaoAssistido"
            "(br.gov.serpro.rtc.api.simplesnacional.model.input.OperacaoSimplesNaoAssistidaInput)")
    lido = _ler_registro(_T_OPERACAO, operacao)
    return DadosOperacao(
        id=lido["id"], versao=lido["versao"], data=lido["data"], tp_doc=lido["tpDoc"], crt=lido["CRT"],
        tp_regime_ibscbs=lido["tpRegimeIBSCBS"], op_simp_nac=lido["opSimpNac"],
        reg_ap_ibscbssn=lido["regApIBSCBSSN"], reg_ap_tribsn=lido["regApTribSN"],
        tp_ente_gov=lido["tpEnteGov"], itens=lido["itens"], aliquotas_efetivas=lido["aliquotasEfetivas"])


# --- Data (LocalDate.parse, ISO_LOCAL_DATE estrito) ---------------------------------------------

_DATA_ISO = re.compile(r"([+-]?)([0-9]{4,10})-([0-9]{2})-([0-9]{2})")


def _bissexto(ano):
    return ano % 4 == 0 and (ano % 100 != 0 or ano % 400 == 0)


def _dias_do_mes(ano, mes):
    if mes == 2:
        return 29 if _bissexto(ano) else 28
    return 30 if mes in (4, 6, 9, 11) else 31


@dataclass(frozen=True)
class DataOperacao:
    """LocalDate da operação.

    `consulta` é a data usada nas consultas à base, com o ano da era (o `yyyy` do Java: o ano 0 e os
    negativos viram 1 - ano); `texto` é o LocalDate.toString do Java (o que vai nas mensagens).
    `ano` é o ano da era: acima de 9999 a oficial falha ao consultar a base (HTTP 500).
    """
    consulta: object
    texto: str
    ano: int


def _parse_data(valor):
    # LocalDate.parse(valor.trim()); None quando o Java lança DateTimeParseException
    achou = _DATA_ISO.fullmatch(_aparar(valor))
    if achou is None:
        return None
    sinal, digitos, mes, dia = achou.groups()
    if (len(digitos) > 4 and sinal == "") or (len(digitos) == 4 and sinal == "+"):  # SignStyle.EXCEEDS_PAD
        return None
    ano = int(digitos) * (-1 if sinal == "-" else 1)
    if abs(ano) > 999_999_999 or (sinal == "-" and ano == 0):
        return None
    mes, dia = int(mes), int(dia)
    if not 1 <= mes <= 12 or not 1 <= dia <= _dias_do_mes(ano, mes):
        return None
    if ano > 9999:
        texto = f"+{ano:d}"
    elif ano < 0:
        texto = f"-{-ano:04d}"
    else:
        texto = f"{ano:04d}"
    texto += f"-{mes:02d}-{dia:02d}"
    era = simples_base.ano_da_era(ano)
    consulta = simples_base.data_de_consulta(era, mes, dia) if era <= 9999 else date(9999, 12, 31)
    return DataOperacao(consulta, texto, era)


# --- Contexto que os itens recebem ----------------------------------------------------------------

@dataclass(frozen=True)
class RegimeInformado:
    """CalculoSimplesUnificadoService.RegimeInformado: campos de regime já convertidos (None se ausentes)."""
    crt: int | None
    tp_regime_ibscbs: int | None
    op_simp_nac: int | None
    reg_ap_ibscbssn: int | None
    reg_ap_tribsn: int | None


@dataclass(frozen=True)
class ParAliquotas:
    """ParAliquotasSimples: alíquotas em pontos percentuais, exatas (Decimal) ou None."""
    p_cbssn: Decimal | None
    p_ibssn: Decimal | None


@dataclass(frozen=True)
class AliquotasAnexo:
    """AliquotasAnexoSimples: o par com redistribuição do ISS e, nos anexos III a V, o par sem."""
    par: ParAliquotas
    sem_redistribuicao_iss: ParAliquotas | None


@dataclass(frozen=True)
class LinhaAliquotas:
    """LinhaAliquotasSimples: a linha de alíquotas do contribuinte. `origem` é sempre "REQUISICAO".

    Cada anexo pode ser None (o anexo não veio); só é exigido o que algum item usa.
    """
    fator_r: bool | None
    impedimento_sublimite: bool
    anexo_i: AliquotasAnexo | None
    anexo_ii: AliquotasAnexo | None
    anexo_iii: AliquotasAnexo | None
    anexo_iv: AliquotasAnexo | None
    anexo_v: AliquotasAnexo | None
    origem: str = "REQUISICAO"

    def anexo(self, anexo):
        # LinhaAliquotasSimples.anexo: numeral romano I a V
        try:
            return {"I": self.anexo_i, "II": self.anexo_ii, "III": self.anexo_iii, "IV": self.anexo_iv,
                    "V": self.anexo_v}[anexo]
        except KeyError:
            raise ValueError(f"Anexo inválido: {anexo}") from None


@dataclass(frozen=True)
class ContextoOperacao:
    """Tudo o que `calcular` apurou da operação até o ponto de processar os itens.

    É o que os itens usam (CalculoSimplesUnificadoService.processar, passos 5 e 6):
      dados          DadosOperacao, a operação como chegou (itens já lidos como EntradaItem)
      data           DataOperacao (use `.consulta` nas consultas à base e `.texto` nas mensagens)
      tp_doc         int, o tpDoc convertido
      documento      linha de TIPO_DFE_CALCULO (vocabularioRegime, casasAliquotaCalculo, maximoItens,
                     admiteImpostoSeletivo, tipoDfeId)
      vocabulario    "CRT" ou "NFSE" (documento["vocabularioRegime"])
      sigla_dfe      sigla do tipo de DF-e vigente (texto) ou None
      permite_ncm_zero  bool
      informado      RegimeInformado
      regime         linha de REGIME_APURACAO (codigo, calculaTributos, geraIbs, ...)
      calcula_tributos  bool, regime["calculaTributos"]
      tp_ente_gov    int ou None
      fator_transicao  Decimal (fração) ou None; só existe com tp_ente_gov diferente de 1
      linha          LinhaAliquotas ou None (None quando o regime não calcula tributos)
      situacoes_iss  lista de SITUACAO_ISS_PAR_ALIQUOTA vigentes (vazia quando não calcula tributos)
      avisos         lista de avisos da operação (avisosDaOperacao), para o bloco resolucaoAliquotas
      base           a Base aberta
      base_url, instancia, agora   como foram dados a `calcular`; o corpo de erro de item os usa
    """
    dados: DadosOperacao
    data: DataOperacao
    tp_doc: int
    documento: dict
    vocabulario: str
    sigla_dfe: str | None
    permite_ncm_zero: bool
    informado: RegimeInformado
    regime: dict
    calcula_tributos: bool
    tp_ente_gov: int | None
    fator_transicao: Decimal | None
    linha: LinhaAliquotas | None
    situacoes_iss: list
    avisos: list
    base: object = field(repr=False)
    base_url: str = ""
    instancia: str = CAMINHO_NAO_ASSISTIDO
    agora: object = None


# --- Validação global (CalculoSimplesUnificadoService) -------------------------------------------

def _validar_campos_globais(dados, erros, base_url):
    # CalculoSimplesUnificadoService.validarCamposGlobais: devolve a data ou None (com VSN-012)
    if not _informado(dados.id):
        erros.append(_erro_global(DADO_GLOBAL_AUSENTE, "id", "O identificador da operação (id) é obrigatório.",
                                  base_url))
    if not _informado(dados.versao):
        erros.append(_erro_global(DADO_GLOBAL_AUSENTE, "versao", "A versão do payload (versao) é obrigatória.",
                                  base_url))
    data = None
    if not _informado(dados.data):
        erros.append(_erro_global(DATA_INVALIDA, "data", "A data do fato gerador é obrigatória.", base_url))
    else:
        data = _parse_data(dados.data)
        if data is None:
            erros.append(_erro_global(
                DATA_INVALIDA, "data",
                f"Data inválida, recebido: '{dados.data}' (esperado YYYY-MM-DD).", base_url))
    if _informado(dados.tp_ente_gov):
        tp_ente_gov = _inteiro(dados.tp_ente_gov)
        if tp_ente_gov is None or tp_ente_gov < 1 or tp_ente_gov > 4:
            erros.append(_erro_global(
                TPENTEGOV_INVALIDO, "tpEnteGov",
                f"Tipo de ente governamental inválido, recebido: '{dados.tp_ente_gov}' (esperado 1, 2, 3 ou 4).",
                base_url))
    if not dados.itens:
        erros.append(_erro_global(ITENS_VAZIOS, "itens",
                                  "A lista de itens é obrigatória e não pode estar vazia.", base_url))
    return data


def _consultar_data(data):
    # A oficial falha (HTTP 500) ao consultar a base com ano acima de 9999
    if data.ano > 9999:
        raise _ErroSistema("Index -1 out of bounds for length 4")
    return data.consulta


def _validar_documento(dados, data, erros, base, base_url):
    # CalculoSimplesUnificadoService.validarDocumento: devolve (linha do documento, tpDoc) ou (None, None)
    if not _informado(dados.tp_doc):
        erros.append(_erro_global(TPDOC_AUSENTE, "tpDoc",
                                  "O tipo de documento fiscal (tpDoc) é obrigatório e não foi informado.",
                                  base_url))
        return None, None
    tp_doc = _inteiro(dados.tp_doc)
    if tp_doc is not None and data is not None and base.tp_doc_sem_tratamento(tp_doc, _consultar_data(data)):
        erros.append(_erro_global(
            TPDOC_NAO_SUPORTADO, "tpDoc",
            f"Tipo de documento fiscal (tpDoc) de código {tp_doc} ainda não possui tratamento "
            "implementado no cálculo do Simples Nacional.", base_url))
        return None, None
    if tp_doc is None or data is None:
        if tp_doc is None:
            erros.append(_erro_global(
                TPDOC_NAO_SUPORTADO, "tpDoc",
                f"Tipo de documento não suportado pelo cálculo do Simples Nacional, recebido: '{dados.tp_doc}'.",
                base_url))
        return None, None
    documento = base.tipo_dfe_calculo(tp_doc, _consultar_data(data))
    if documento is None:
        erros.append(_erro_global(
            TPDOC_NAO_SUPORTADO, "tpDoc",
            f"Tipo de documento não suportado pelo cálculo do Simples Nacional, recebido: '{dados.tp_doc}'.",
            base_url))
        return None, None
    return documento, tp_doc


def _validar_quantidade_itens(dados, documento, erros, base_url):
    # CalculoSimplesUnificadoService.validarQuantidadeItens: acima do máximo do documento, VSN-034
    maximo = documento["maximoItens"]
    if maximo is not None and dados.itens is not None and len(dados.itens) > maximo:
        limite = "exatamente 1 item" if maximo == 1 else f"no máximo {maximo} itens"
        erros.append(_erro_global(
            QUANTIDADE_ITENS_INVALIDA, "itens",
            f"O documento {_aparar(dados.tp_doc)} admite {limite}, recebidos {len(dados.itens)}.", base_url))


def _validar_regime(dados, vocabulario, erros, base_url):
    # CalculoSimplesUnificadoService.validarRegime: campos de regime no vocabulário do documento
    if vocabulario == "NFSE":
        _proibido(dados.crt, "CRT", "NFS-e", erros, base_url)
        _proibido(dados.tp_regime_ibscbs, "tpRegimeIBSCBS", "NFS-e", erros, base_url)
        _validar_regime_nfse(dados, erros, base_url)
    else:
        _proibido(dados.op_simp_nac, "opSimpNac", "documento com CRT", erros, base_url)
        _proibido(dados.reg_ap_ibscbssn, "regApIBSCBSSN", "documento com CRT", erros, base_url)
        _proibido(dados.reg_ap_tribsn, "regApTribSN", "documento com CRT", erros, base_url)
        _validar_regime_crt(dados, erros, base_url)


def _proibido(valor, campo, documento, erros, base_url):
    # CalculoSimplesUnificadoService.proibido (VSN-033)
    if _informado(valor):
        erros.append(_erro_global(
            CAMPO_REGIME_PROIBIDO, campo,
            f"O campo {campo} não pertence ao vocabulário de regime do {documento}, recebido: '{valor}'.",
            base_url))


def _validar_regime_crt(dados, erros, base_url):
    # CalculoSimplesUnificadoService.validarRegimeCrt: família CRT (VSN-016, VSN-017, VSN-018)
    crt = _inteiro(dados.crt)
    if crt is None or crt < 1 or crt > 4:
        erros.append(_erro_global(
            CRT_INVALIDO, "CRT",
            "O Código de Regime Tributário (CRT) é obrigatório e deve ser 1, 2, 3 ou 4, "
            f"recebido: '{_s(dados.crt)}'.", base_url))
        return
    if not _informado(dados.tp_regime_ibscbs):
        return
    tp_regime = _inteiro(dados.tp_regime_ibscbs)
    if tp_regime is None or (tp_regime != 1 and tp_regime != 2):
        erros.append(_erro_global(
            TPREGIMEIBSCBS_INVALIDO, "tpRegimeIBSCBS",
            f"Tipo de regime de apuração do IBS/CBS inválido, recebido: '{dados.tp_regime_ibscbs}' "
            "(esperado 1 ou 2).", base_url))
        return
    if crt == 4:
        erros.append(_erro_global(
            TPREGIMEIBSCBS_INCOMPATIVEL, "tpRegimeIBSCBS",
            "CRT=4 (MEI) não admite tpRegimeIBSCBS algum (regras C21a-10 e C21a-30 da NT 2026.005)."
            if tp_regime == 2 else
            "CRT=4 (MEI) não admite tpRegimeIBSCBS algum (regra C21a-30 da NT 2026.005).", base_url))
    elif (crt == 1 or crt == 2) and tp_regime == 2:
        erros.append(_erro_global(
            TPREGIMEIBSCBS_INCOMPATIVEL, "tpRegimeIBSCBS",
            f"CRT={crt} não admite tpRegimeIBSCBS=2 (regra C21a-10 da NT 2026.005).", base_url))
    elif crt == 3 and tp_regime == 1:
        erros.append(_erro_global(
            TPREGIMEIBSCBS_INCOMPATIVEL, "tpRegimeIBSCBS",
            "CRT=3 não admite tpRegimeIBSCBS=1 (regra C21a-20 da NT 2026.005).", base_url))


_OPTANTES = (3, 4)  # opSimpNac 3 (ME/EPP) e 4 (opção pendente)


def _validar_regime_nfse(dados, erros, base_url):
    # CalculoSimplesUnificadoService.validarRegimeNfse: opSimpNac (VSN-030), regApIBSCBSSN (VSN-031),
    # regApTribSN (VSN-032)
    op_simp_nac = _inteiro(dados.op_simp_nac)
    if op_simp_nac is None or op_simp_nac < 1 or op_simp_nac > 4:
        erros.append(_erro_global(
            OPSIMPNAC_INVALIDO, "opSimpNac",
            "A situação perante o Simples Nacional (opSimpNac) é obrigatória na NFS-e e deve ser "
            f"1, 2, 3 ou 4, recebido: '{_s(dados.op_simp_nac)}'.", base_url))
        return
    _validar_campo_nfse(dados.reg_ap_ibscbssn, "regApIBSCBSSN", op_simp_nac, REGAPIBSCBSSN_INVALIDO,
                        "regra 313 da NFS-e", erros, base_url)
    _validar_campo_nfse(dados.reg_ap_tribsn, "regApTribSN", op_simp_nac, REGAPTRIBSN_INVALIDO,
                        "regra E0166 da NFS-e, estendida pela NT 009", erros, base_url)


def _validar_campo_nfse(valor, campo, op_simp_nac, codigo, regra_obrigatorio, erros, base_url):
    # CalculoSimplesUnificadoService.validarCampoNfse
    if _informado(valor):
        convertido = _inteiro(valor)
        if convertido is None or convertido < 1 or convertido > 3:
            erros.append(_erro_global(codigo, campo, f"O {campo} deve ser 1, 2 ou 3, recebido: '{valor}'.",
                                      base_url))
        elif op_simp_nac not in _OPTANTES:
            sufixo = " (regra E0162 da NFS-e)" if campo == "regApTribSN" else ""
            erros.append(_erro_global(
                codigo, campo,
                f"O {campo} não pode ser informado com opSimpNac={op_simp_nac} (não optante ou MEI){sufixo}.",
                base_url))
    elif op_simp_nac in _OPTANTES:
        erros.append(_erro_global(
            codigo, campo, f"O {campo} é obrigatório com opSimpNac={op_simp_nac} ({regra_obrigatorio}).",
            base_url))


def _converter_regime(dados):
    # CalculoSimplesUnificadoService.converterRegime
    return RegimeInformado(
        crt=_inteiro(dados.crt), tp_regime_ibscbs=_inteiro(dados.tp_regime_ibscbs),
        op_simp_nac=_inteiro(dados.op_simp_nac), reg_ap_ibscbssn=_inteiro(dados.reg_ap_ibscbssn),
        reg_ap_tribsn=_inteiro(dados.reg_ap_tribsn))


def _descrever_regime(vocabulario, informado):
    # CalculoSimplesUnificadoService.descreverRegime
    if vocabulario == "NFSE":
        return f"opSimpNac={_d(informado.op_simp_nac)}, regApIBSCBSSN={_d(informado.reg_ap_ibscbssn)}"
    if informado.tp_regime_ibscbs is None:
        return f"CRT={_d(informado.crt)}"
    return f"CRT={_d(informado.crt)}, tpRegimeIBSCBS={informado.tp_regime_ibscbs}"


def _erro_fora_do_escopo(vocabulario, informado, data, base_url):
    # CalculoSimplesUnificadoService.erroForaDoEscopo (VSN-019)
    if vocabulario == "NFSE":
        campo = "opSimpNac"
        if informado.op_simp_nac == 1:
            motivo = "prestador não optante pelo Simples Nacional (opSimpNac=1)"
        elif informado.op_simp_nac == 2:
            motivo = "MEI (opSimpNac=2) está fora do cálculo do IBS e da CBS pelo Simples Nacional"
        else:
            motivo = (f"não há enquadramento vigente na data '{data.texto}' para opSimpNac="
                      f"{_d(informado.op_simp_nac)} e regApIBSCBSSN={_d(informado.reg_ap_ibscbssn)}")
    else:
        campo = "CRT"
        if informado.crt == 4:
            motivo = "MEI (CRT=4) está fora do Evento de Tributação do Simples Nacional"
        elif informado.crt == 3 and informado.tp_regime_ibscbs is None:
            motivo = "CRT=3 sem tpRegimeIBSCBS=2 (regime normal puro) não é Simples Nacional"
        else:
            motivo = (f"não há enquadramento vigente na data '{data.texto}' para "
                      f"{_descrever_regime(vocabulario, informado)}")
    return _erro_global(OPERACAO_FORA_ESCOPO, campo,
                        "Operação fora do escopo do cálculo do Simples Nacional: " + motivo + ".", base_url)


def _avisos_da_operacao(vocabulario, regime, informado, linha):
    # CalculoSimplesUnificadoService.avisosDaOperacao
    avisos = []
    if linha is not None:
        gera_ibs = regime["geraIbs"] is True
        if gera_ibs == linha.impedimento_sublimite:
            avisos.append(
                f"Regime de apuração {regime['codigo']} ({_descrever_regime(vocabulario, informado)}) diverge "
                "do indicador de impedimento por sublimite da linha de alíquotas "
                f"({'true' if linha.impedimento_sublimite else 'false'})")
    if informado.reg_ap_ibscbssn == 2 and informado.reg_ap_tribsn == 1:
        avisos.append("regApIBSCBSSN 2 (excesso de sublimite) com regApTribSN 1: incoerente \u2014 quem excede "
                      "o sublimite tem o ISS fora do Simples Nacional")
    return avisos


# --- Linha de alíquotas no corpo (SimplesNacionalNaoAssistidoService.FonteRequisicao) -------------

_RAIZ_ALIQUOTAS = "aliquotasEfetivas"
# ProcessadorItemSimples.FORMATO_ALIQUOTA: até 3 dígitos inteiros e até 34 casas, ASCII, sem sinal
_FORMATO_ALIQUOTA = re.compile(r"[0-9]{1,3}(\.[0-9]{1,34})?")
_DESCRICAO_FORMATO_ALIQUOTA = ("até 3 dígitos inteiros e até 34 casas decimais, sem notação científica "
                               "(ex.: 1.6839)")  # ProcessadorItemSimples.DESCRICAO_FORMATO_ALIQUOTA


def _aliquota(valor, caminho, codigo, erros, base_url):
    # FonteRequisicao.aliquota: alíquota em pontos percentuais com todas as casas; fora do formato, erro global
    if not _informado(valor):
        return None
    texto = _aparar(valor)
    if _FORMATO_ALIQUOTA.fullmatch(texto):
        return Decimal(texto)
    negativo = texto.startswith("-") and _FORMATO_ALIQUOTA.fullmatch(texto[1:]) is not None
    erros.append(_erro_global(
        codigo, _RAIZ_ALIQUOTAS + "/" + caminho,
        f"O valor não pode ser negativo, recebido: '{valor}'." if negativo else
        f"O valor não é um número decimal válido, recebido: '{valor}'. Informe {_DESCRICAO_FORMATO_ALIQUOTA}.",
        base_url))
    return None


def _par(p_cbssn, p_ibssn, caminho, erros, base_url):
    # FonteRequisicao.par: CBS e depois IBS
    cbs = _aliquota(p_cbssn, caminho + "/pCBSSN", ALIQUOTA_CBS_INVALIDA, erros, base_url)
    ibs = _aliquota(p_ibssn, caminho + "/pIBSSN", ALIQUOTA_IBS_INVALIDA, erros, base_url)
    return ParAliquotas(cbs, ibs)


def _anexo_par_unico(anexo, caminho, erros, base_url):
    # FonteRequisicao.anexo(AnexoParUnicoInput)
    if anexo is None:
        return None
    return AliquotasAnexo(_par(anexo["pCBSSN"], anexo["pIBSSN"], caminho, erros, base_url), None)


def _anexo_com_iss(anexo, caminho, erros, base_url):
    # FonteRequisicao.anexo(AnexoComIssInput)
    if anexo is None:
        return None
    sem = anexo["semRedistribuicaoISS"]
    sem_informado = sem is not None and (_informado(sem["pCBSSN"]) or _informado(sem["pIBSSN"]))
    principal = _par(anexo["pCBSSN"], anexo["pIBSSN"], caminho, erros, base_url)
    return AliquotasAnexo(
        principal,
        _par(sem["pCBSSN"], sem["pIBSSN"], caminho + "/semRedistribuicaoISS", erros, base_url)
        if sem_informado else None)


def _obter_linha(aliquotas, base_url):
    # FonteRequisicao.obter: só é chamada quando o regime calcula tributos
    if aliquotas is None:
        raise _ErrosCalculo([_erro_global(
            ALIQUOTAS_EFETIVAS_AUSENTES, _RAIZ_ALIQUOTAS,
            "A linha de alíquotas efetivas do contribuinte (aliquotasEfetivas) é obrigatória no cálculo "
            "não assistido quando o regime calcula tributos.", base_url)])
    erros = []
    linha = LinhaAliquotas(
        fator_r=aliquotas["fatorR"],
        impedimento_sublimite=aliquotas["impedimentoSublimite"] is True,
        anexo_i=_anexo_par_unico(aliquotas["anexoI"], "anexoI", erros, base_url),
        anexo_ii=_anexo_par_unico(aliquotas["anexoII"], "anexoII", erros, base_url),
        anexo_iii=_anexo_com_iss(aliquotas["anexoIII"], "anexoIII", erros, base_url),
        anexo_iv=_anexo_com_iss(aliquotas["anexoIV"], "anexoIV", erros, base_url),
        anexo_v=_anexo_com_iss(aliquotas["anexoV"], "anexoV", erros, base_url))
    if erros:
        raise _ErrosCalculo(erros)
    return linha


# --- Processamento da operação -------------------------------------------------------------------

def _preparar_operacao(dados, base, base_url, instancia, agora):
    # CalculoSimplesUnificadoService.processar, passos 1 a 4
    erros = []
    data = _validar_campos_globais(dados, erros, base_url)
    documento, tp_doc = _validar_documento(dados, data, erros, base, base_url)
    if documento is not None:
        _validar_regime(dados, documento["vocabularioRegime"], erros, base_url)
        _validar_quantidade_itens(dados, documento, erros, base_url)
    if erros:
        raise _ErrosCalculo(erros)
    vocabulario = documento["vocabularioRegime"]
    consulta = _consultar_data(data)
    sigla_dfe = base.sigla_tipo_dfe_vigente(documento["tipoDfeId"], consulta)
    permite_ncm_zero = base.permite_ncm_zero(documento["tipoDfeId"], consulta) is True
    informado = _converter_regime(dados)

    # 2. Regime de apuração (código neutro) pela tabela de enquadramento.
    regime = base.regime_apuracao(vocabulario, informado.crt, informado.tp_regime_ibscbs,
                                  informado.op_simp_nac, informado.reg_ap_ibscbssn, consulta)
    if regime is None:
        raise _ErrosCalculo([_erro_fora_do_escopo(vocabulario, informado, data, base_url)])
    calcula_tributos = regime["calculaTributos"] is True

    # 3. Fator de transição de compra governamental.
    tp_ente_gov = _inteiro(dados.tp_ente_gov)
    fator_transicao = None
    if tp_ente_gov is not None and tp_ente_gov != 1:
        fator_transicao = base.fator_transicao_compra_gov(consulta)
        if fator_transicao is None:
            raise _ErrosCalculo([_erro_global(
                COMPRA_GOV_SEM_VIGENCIA, "tpEnteGov",
                "Não há parametrização de transição de compra governamental vigente "
                f"para a data '{data.texto}'.", base_url)])

    # 4. Linha de alíquotas: só quando o regime calcula tributos.
    linha = _obter_linha(dados.aliquotas_efetivas, base_url) if calcula_tributos else None
    situacoes = base.situacoes_iss_par_aliquota(consulta) if calcula_tributos else []
    avisos = _avisos_da_operacao(vocabulario, regime, informado, linha)

    return ContextoOperacao(
        dados=dados, data=data, tp_doc=tp_doc, documento=documento, vocabulario=vocabulario,
        sigla_dfe=sigla_dfe, permite_ncm_zero=permite_ncm_zero, informado=informado, regime=regime,
        calcula_tributos=calcula_tributos, tp_ente_gov=tp_ente_gov, fator_transicao=fator_transicao,
        linha=linha, situacoes_iss=situacoes, avisos=avisos, base=base, base_url=base_url,
        instancia=instancia, agora=agora)


def _processar_itens(ctx):
    # CalculoSimplesUnificadoService.processar, passos 5 e 6. O Java processa os itens em paralelo e
    # devolve na ordem de entrada; aqui a ordem é a mesma, em sequência.
    fatos_operacao = _fatos_da_operacao(ctx.informado)
    processados = []
    for indice, bruto in enumerate(ctx.dados.itens):
        item = _item_seguro(bruto)
        resolvedor = (_Resolvedor(item, ctx, fatos_operacao) if ctx.calcula_tributos else _SEM_ALIQUOTAS)
        processados.append(_processar_item(item, indice, ctx, resolvedor))

    # objetos em ordem de nObj (nulo primeiro); ordenação estável, como Stream.sorted
    objetos = sorted((p.objeto for p in processados), key=lambda o: (o.n_obj is not None, o.n_obj or 0))
    resolucao = None
    if ctx.calcula_tributos:
        resolucao = _resolucao_json(ctx.linha, [p.resolucao for p in processados], ctx.avisos)

    corpo = {"oper": _operacao_json(ctx.dados, ctx.regime["codigo"])}
    if resolucao is not None:
        corpo["resolucaoAliquotas"] = resolucao
    corpo["objetos"] = [_objeto_json(o) for o in objetos]
    total = _totalizar(objetos)
    if total is not None:
        corpo["total"] = total
    return 200, corpo


def _fatos_da_operacao(informado):
    # CalculoSimplesUnificadoService.fatosDaOperacao: campos do documento que entram na escolha do par
    fatos = {}
    _colocar(fatos, _CAMPO_CRT, informado.crt)
    _colocar(fatos, _CAMPO_REGAPIBSCBSSN, informado.reg_ap_ibscbssn)
    _colocar(fatos, _CAMPO_REGAPTRIBSN, informado.reg_ap_tribsn)
    return fatos


def _colocar(fatos, campo, valor):
    # CalculoSimplesUnificadoService.colocar
    if valor is not None:
        fatos[campo] = valor


def _item_seguro(item):
    # CalculoSimplesUnificadoService.itemSeguro: item nulo na lista vira item vazio
    return item if item is not None else EntradaItem()


# --- Estados, motivos e erros do item ----------------------------------------------------------

CALCULADO = "CALCULADO"  # EstadoItemSimplesEnum
NAO_CALCULADO = "NAO_CALCULADO"
NAO_IMPLEMENTADO = "NAO_IMPLEMENTADO"
INCONSISTENCIA_ENTRADA = "INCONSISTENCIA_ENTRADA"
FALHA_TECNICA_ESTADO = "FALHA_TECNICA"

# MotivoNaoCalculoSimplesEnum: código e descrição base (com %s onde o Java formata)
_MOTIVOS_NAO_CALCULO = {
    "REGIME_SOMENTE_RECEITA": (
        "O optante apura o IBS e a CBS pelo regime regular, fora do Simples Nacional; o item leva ao Simples "
        "apenas a receita bruta."),
    "CATIVSN_AUSENTE": (
        "O código de atividade do Simples Nacional (cAtivSN) não foi informado; sem ele o IBS e a CBS do item "
        "não são calculados (NT 2026.005, §5.3.3)."),
    "CATIVSN_DESCONHECIDO": (
        "O código de atividade '%s' não existe ou não está vigente na data do fato gerador; o IBS e a CBS do "
        "item não são calculados."),
    "CATIVSN_INADMISSIVEL_NO_DOCUMENTO": (
        "A atividade '%s' não é admitida no tipo de documento %s (tpDoc %s); o IBS e a CBS do item não são "
        "calculados."),
    "ANEXO_SEM_ALIQUOTA_CBS": (
        "A Tabela de Alíquotas Efetivas do Simples Nacional não traz a alíquota de CBS do Anexo %s para o "
        "contribuinte no período; o IBS e a CBS do item não são calculados."),
}


def _motivo(codigo, *parametros):
    # MotivoNaoCalculoSimplesEnum.motivo: {codigo, descricao}, com os %s trocados pelos parâmetros
    descricao = _MOTIVOS_NAO_CALCULO[codigo]
    if parametros:
        descricao = descricao % tuple(_s(p) if p is None or isinstance(p, str) else str(p) for p in parametros)
    return {"codigo": codigo, "descricao": descricao}


class _ColetorItem:
    """ItemCollectorSimples: erros do item (RFC 7807) e o estado que eles dão ao item."""

    def __init__(self, indice, base_url):
        self.indice = indice
        self.base_url = base_url
        self.estado = CALCULADO
        self.erros = []

    def erro(self, codigo, campo, detalhe):
        # addErroValidacao: campo relativo ao item (None aponta o item)
        self.erro_no_ponteiro(codigo, self._ponteiro(campo), detalhe)

    def erro_no_ponteiro(self, codigo, ponteiro, detalhe):
        # addErroValidacaoNoPonteiro: o primeiro erro leva o item a INCONSISTENCIA_ENTRADA
        if self.estado == CALCULADO:
            self.estado = INCONSISTENCIA_ENTRADA
        self._acumular(codigo, ponteiro, detalhe)

    def nao_implementado(self, codigo, campo, detalhe):
        # addNaoImplementado: o estado vale sempre
        self.estado = NAO_IMPLEMENTADO
        self._acumular(codigo, self._ponteiro(campo), detalhe)

    def falha_tecnica(self, mensagem):
        # addFalhaTecnica: VSN-999 no próprio item, com a mensagem da exceção
        self.estado = FALHA_TECNICA_ESTADO
        self._acumular(FALHA_TECNICA, f"#/itens/{self.indice}", mensagem)

    def tem_erros(self):
        return bool(self.erros)

    def _ponteiro(self, campo):
        return f"#/itens/{self.indice}" if campo is None else f"#/itens/{self.indice}/{campo}"

    def _acumular(self, codigo, ponteiro, detalhe):
        # ErroDetalhe, com NON_NULL: detalhe nulo sai do JSON
        erro = {"type": f"{self.base_url}{CAMINHO_ERROS}?codigo={codigo}", "title": CODIGOS[codigo]["titulo"],
                "code": codigo, "pointer": ponteiro}
        if detalhe is not None:
            erro["detail"] = detalhe
        self.erros.append(erro)


class _FalhaTecnica(Exception):
    """RuntimeException fora das críticas conhecidas: vira VSN-999 no item, com esta mensagem."""


def _mensagem_da_excecao(erro):
    # Throwable.getMessage: nulo quando a exceção nasce sem mensagem (o detalhe sai do JSON)
    return str(erro) if erro.args else None


# --- Resultado do item (ProcessadorItemSimples, SimplesNacionalCalculoService) ------------------

@dataclass(frozen=True)
class Tributos:
    """TributosSimplesDomain: valores exatos (Decimal) ou None; None sai do JSON (NON_NULL)."""
    v_rbsn_item: Decimal
    tp_rbsn: int
    tp_rbsn_descricao: str
    v_bc: Decimal | None = None
    p_ibssn: Decimal | None = None
    v_ibssn: Decimal | None = None
    v_ibs_pend_susp: Decimal | None = None
    p_cbssn: Decimal | None = None
    v_cbssn: Decimal | None = None
    v_cbs_pend_susp: Decimal | None = None
    imposto_seletivo: Decimal | None = None
    total_item: Decimal | None = None
    memoria_cbs: str | None = None
    memoria_ibs: str | None = None


@dataclass(frozen=True)
class Objeto:
    """ObjetoSimplesDomain: nObj, estado, motivo (só no NAO_CALCULADO), tributos e erros."""
    n_obj: int | None
    estado: str
    motivo: dict | None = None
    trib_calc: Tributos | None = None
    erros: list | None = None


@dataclass(frozen=True)
class ResolucaoItem:
    """ResolucaoAliquotaItemDomain: a escolha do par do item, para o bloco resolucaoAliquotas."""
    numero: int | None
    c_ativ_sn: str | None
    anexo: str | None
    tipo_aliquota: str | None
    regra: str | None
    p_ibssn: Decimal | None
    p_cbssn: Decimal | None
    motivo: str
    avisos: tuple = ()


@dataclass(frozen=True)
class AliquotasItem:
    """ProcessadorItemSimples.AliquotasItemSimples: o par do cálculo (pontos percentuais) e a resolução."""
    p_ibssn: Decimal | None
    p_cbssn: Decimal | None
    nao_calcular: bool
    resolucao: ResolucaoItem | None


@dataclass(frozen=True)
class AtividadeItem:
    """ProcessadorItemSimples.AtividadeItemSimples: cAtivSN normalizado, linha vigente e admissão no tpDoc."""
    c_ativ_sn: str | None
    atividade: dict | None
    admissivel: bool

    def valida(self):
        return self.atividade is not None and self.admissivel


@dataclass(frozen=True)
class ItemProcessado:
    """ProcessadorItemSimples.ItemProcessadoSimples."""
    objeto: Objeto
    resolucao: ResolucaoItem | None


# --- Processamento de um item (ProcessadorItemSimples) ----------------------------------------

_CAMPO_CCLASSTRIB = "cClassTrib"
_UM_DIGITO = re.compile(r"[0-9]")  # \d do java.util.regex, que é só ASCII
_FORMATO_VALOR = re.compile(r"[0-9]{1,13}(\.[0-9]{1,2})?")  # 13v2
_DESCRICAO_FORMATO_VALOR = ("até 13 dígitos inteiros e até 2 casas decimais, sem notação científica "
                            "(ex.: 1000.00)")


def _processar_item(item, indice, ctx, resolvedor):
    # ProcessadorItemSimples.processarItem: erros de validação ficam no próprio item, nunca na operação
    coletor = _ColetorItem(indice, ctx.base_url)
    data = ctx.data.consulta

    n_obj = item.numero
    if n_obj is None:
        coletor.erro(NUMERO_AUSENTE, "numero", "O número do item é obrigatório.")

    # 0. Prioridade máxima: cClassTrib ainda sem tratamento na calculadora (VSN-027).
    if _c_class_trib_nao_implementado(item.c_class_trib, ctx, coletor):
        return ItemProcessado(Objeto(n_obj if n_obj is not None else indice + 1, coletor.estado,
                                     erros=list(coletor.erros)), resolvedor.nao_avaliado())

    # NCM zero vale como ausente só no DF-e que o permite
    ncm = None if ctx.permite_ncm_zero and item.ncm in ("00", "00000000") else item.ncm

    # 1. Insumos monetários; validação estrutural de NCM/NBS depois da base.
    base_calculo = _validar_decimal(item.base_calculo, coletor, "baseCalculo", BASE_AUSENTE, BASE_INVALIDA)
    estrutura_valida = _validar_estrutural(ncm, item.nbs, ctx, coletor)
    if ctx.documento["admiteImpostoSeletivo"] is True:
        valor_is = _validar_decimal(item.valor_imposto_seletivo, coletor, "valorImpostoSeletivo", IS_INVALIDO,
                                    IS_INVALIDO)
    else:
        valor_is = _validar_imposto_seletivo_nao_admitido(item.valor_imposto_seletivo, coletor)

    # 2. Tipo de receita bruta pelo cClassTrib, e a nomenclatura pela classificação.
    classificacao, tipo_receita = _resolver_tipo_receita_bruta(item.c_class_trib, ctx, coletor)
    _validar_por_classificacao(ncm, item.nbs, ctx, classificacao, estrutura_valida, coletor)

    calcula_tributos = ctx.regime["calculaTributos"] is not False
    atividade = AtividadeItem(None, None, False)
    aliquotas = AliquotasItem(None, None, False, None)
    if calcula_tributos:
        # 3. cAtivSN: inválido não é erro, leva a NAO_CALCULADO.
        atividade = _validar_c_ativ_sn(item.c_ativ_sn, tipo_receita, ctx, coletor)
        # 4. Alíquotas exigidas só quando o cálculo as usa.
        cbs_exigida = (atividade.valida() and tipo_receita is not None
                       and tipo_receita["exigeAliquota"] is True)
        ibs_exigida = cbs_exigida and ctx.regime["geraIbs"] is True
        aliquotas = resolvedor.resolver(atividade, cbs_exigida, ibs_exigida, coletor)

    # 5. Com erro de validação, não calcula.
    if coletor.tem_erros():
        return ItemProcessado(Objeto(n_obj if n_obj is not None else indice + 1, coletor.estado,
                                     erros=list(coletor.erros)), aliquotas.resolucao)

    # 6. Cálculo, com tudo resolvido.
    motivo_nao_calculo = _motivo_nao_calculo(atividade, aliquotas, ctx) if calcula_tributos else None
    try:
        estado, motivo, tributos = _calcular_item(
            ctx.regime, tipo_receita, motivo_nao_calculo, base_calculo, valor_is, aliquotas.p_ibssn,
            aliquotas.p_cbssn, ctx.tp_ente_gov, ctx.fator_transicao, ctx.documento["casasAliquotaCalculo"])
    except simples_base.BaseIndisponivel:
        raise  # arquivo da base inutilizável: não existe na oficial, a skill avisa em vez de calcular
    except Exception as erro:  # catch (Exception ex) do Java: qualquer falha do cálculo vira VSN-999 no item
        coletor.falha_tecnica(_mensagem_da_excecao(erro))
        return ItemProcessado(Objeto(n_obj if n_obj is not None else indice + 1, coletor.estado,
                                     erros=list(coletor.erros)), aliquotas.resolucao)
    return ItemProcessado(Objeto(n_obj, estado, motivo, tributos), aliquotas.resolucao)


def _motivo_nao_calculo(atividade, aliquotas, ctx):
    # ProcessadorItemSimples.motivoNaoCalculo: None quando o item é calculável
    if atividade.c_ativ_sn is None:
        return _motivo("CATIVSN_AUSENTE")
    if atividade.atividade is None:
        return _motivo("CATIVSN_DESCONHECIDO", atividade.c_ativ_sn)
    if not atividade.admissivel:
        documento = ctx.sigla_dfe if ctx.sigla_dfe is not None else "informado"
        return _motivo("CATIVSN_INADMISSIVEL_NO_DOCUMENTO", atividade.c_ativ_sn, documento, ctx.tp_doc)
    if aliquotas.nao_calcular:
        anexo = aliquotas.resolucao.anexo if aliquotas.resolucao is not None else None
        return _motivo("ANEXO_SEM_ALIQUOTA_CBS", anexo if anexo is not None else "aplicado")
    return None


def _normalizar_c_ativ_sn(c_ativ_sn, completar_zero):
    # ProcessadorItemSimples.normalizarCAtivSN: aparado, "7" vira "07"; None quando ausente ou em branco
    if not _informado(c_ativ_sn):
        return None
    codigo = _aparar(c_ativ_sn)
    return "0" + codigo if completar_zero and _UM_DIGITO.fullmatch(codigo) else codigo


def _c_class_trib_nao_implementado(c_class_trib, ctx, coletor):
    # ProcessadorItemSimples.cClassTribNaoImplementado: a primeira consulta de banco do item
    if not _informado(c_class_trib):
        return False
    codigo = _aparar(c_class_trib)
    if ctx.base.contar_tratamentos_nao_implementados(codigo, ctx.data.consulta) == 0:
        return False
    coletor.nao_implementado(
        CCLASSTRIB_NAO_IMPLEMENTADO, _CAMPO_CCLASSTRIB,
        f"A classificação tributária '{codigo}' ainda não possui tratamento implementado no cálculo do "
        f"Simples Nacional na data '{ctx.data.texto}'. Essa funcionalidade está prevista no roadmap e será "
        "disponibilizada em versão futura.")
    return True


def _resolver_tipo_receita_bruta(c_class_trib, ctx, coletor):
    # ProcessadorItemSimples.resolverTipoReceitaBruta: devolve (classificação, tipo de receita) ou
    # (None, None) quando a classificação está ausente, desconhecida ou incompatível com o tpDoc
    if not _informado(c_class_trib):
        coletor.erro(CCLASSTRIB_AUSENTE, _CAMPO_CCLASSTRIB,
                     "O código de classificação tributária (cClassTrib) é obrigatório e não foi informado.")
        return None, None
    codigo = _aparar(c_class_trib)
    data = ctx.data.consulta
    vigentes = ctx.base.classificacao(codigo, data)
    if not vigentes:
        coletor.erro(CCLASSTRIB_DESCONHECIDO, _CAMPO_CCLASSTRIB,
                     f"O cClassTrib '{codigo}' não existe ou não está vigente na data '{ctx.data.texto}': "
                     "o tpRBSN não pôde ser determinado.")
        return None, None
    classificacao = vigentes[0]
    if ctx.base.contar_vinculos_classificacao_tp_doc(codigo, ctx.tp_doc, data) == 0:
        coletor.erro(CCLASSTRIB_INCOMPATIVEL_TPDOC, _CAMPO_CCLASSTRIB,
                     f"O cClassTrib '{codigo}' não é admissível para o tipo de documento fiscal {ctx.tp_doc}.")
        return None, None
    tp_rbsn = classificacao["tpRBSN"]
    tipo_receita = ctx.base.tipo_receita_bruta(tp_rbsn, data, ctx.vocabulario)
    if tipo_receita is None:
        coletor.erro(PARAMETRIZACAO_INDISPONIVEL, _CAMPO_CCLASSTRIB,
                     f"Não há parametrização de efeito vigente para o tipo de receita bruta tpRBSN={tp_rbsn} "
                     f"na data '{ctx.data.texto}'.")
    return classificacao, tipo_receita


def _validar_c_ativ_sn(c_ativ_sn, tipo_receita, ctx, coletor):
    # ProcessadorItemSimples.validarCAtivSN: só o VSN-023 é erro; o resto leva a NAO_CALCULADO
    codigo = _normalizar_c_ativ_sn(c_ativ_sn, True)
    if codigo is None:
        return AtividadeItem(None, None, False)
    data = ctx.data.consulta
    atividades = ctx.base.atividades_por_c_ativ_sn(codigo, data)
    if not atividades:
        return AtividadeItem(codigo, None, False)
    atividade = atividades[0]
    if ctx.base.contar_vinculos_atividade_tp_doc(atividade["id"], ctx.tp_doc, data) == 0:
        return AtividadeItem(codigo, atividade, False)
    if (atividade["naoTributavel"] is True and tipo_receita is not None
            and tipo_receita["vedadoNaoTributavel"] is True):
        coletor.erro(CATIVSN_INCOMPATIVEL_CCLASSTRIB, "cAtivSN",
                     f"O código de atividade '{codigo}' (Operações não Tributáveis) é incompatível com "
                     f"cClassTrib de tpRBSN={tipo_receita['codigo']}.")
    return AtividadeItem(codigo, atividade, True)


def _validar_decimal(valor, coletor, campo, codigo_ausente, codigo_invalido):
    # ProcessadorItemSimples.validarDecimal: valor monetário obrigatório, 13v2, não negativo
    if not _informado(valor):
        coletor.erro(codigo_ausente, campo, "O campo é obrigatório e não foi informado.")
        return None
    return _converter_no_formato(valor, _FORMATO_VALOR, _DESCRICAO_FORMATO_VALOR, coletor, campo, codigo_invalido)


def _validar_imposto_seletivo_nao_admitido(valor, coletor):
    # ProcessadorItemSimples.validarImpostoSeletivoNaoAdmitido: ausente ou zero; devolve sempre None
    if not _informado(valor):
        return None
    convertido = _converter_no_formato(valor, _FORMATO_VALOR, _DESCRICAO_FORMATO_VALOR, coletor,
                                       "valorImpostoSeletivo", IS_INVALIDO)
    if convertido is not None and not convertido.is_zero():
        coletor.erro(IS_NAO_ADMITIDO, "valorImpostoSeletivo",
                     "O documento não tem Imposto Seletivo: o valor deve ser zero ou não ser informado, "
                     f"recebido: '{valor}'.")
    return None


def _converter_no_formato(valor, formato, descricao_formato, coletor, campo, codigo_invalido):
    # ProcessadorItemSimples.converterNoFormato: o formato é conferido antes da conversão
    texto = _aparar(valor)
    if formato.fullmatch(texto):
        return Decimal(texto)
    if texto.startswith("-") and formato.fullmatch(texto[1:]):
        coletor.erro(codigo_invalido, campo, f"O valor não pode ser negativo, recebido: '{valor}'.")
    else:
        coletor.erro(codigo_invalido, campo,
                     f"O valor não é um número decimal válido, recebido: '{valor}'. Informe {descricao_formato}.")
    return None


# --- Nomenclatura do item (ValidacaoNomenclaturaAdapter) ----------------------------------------

_CRITICAS_NOMENCLATURA = (  # exceção conhecida -> (código, campo); campo "?" é decidido pelo valor
    (simples_base.NcmNbsSimultaneasException, NCM_NBS_SIMULTANEOS, "ncm"),
    (simples_base.NcmNaoEncontradaException, NCM_NAO_ENCONTRADO, "ncm"),
    (simples_base.NbsNaoEncontradaException, NBS_NAO_ENCONTRADO, "nbs"),
    (simples_base.NcmCompletoNaoInformadoException, NCM_INCOMPLETO, "ncm"),
    (simples_base.NbsCompletoNaoInformadoException, NBS_INCOMPLETO, "nbs"),
    (simples_base.NomenclaturaObrigatoriaNaoInformadaException, NOMENCLATURA_OBRIGATORIA, None),
    (simples_base.NomenclaturaIncompativelTipoDfeException, NOMENCLATURA_INCOMPATIVEL, "?"),
    (simples_base.NomenclaturaNaoPermitidaTipoDfeException, NOMENCLATURA_NAO_PERMITIDA, None),
    (simples_base.NcmNaoVinculadaException, NCM_NAO_APLICAVEL, "ncm"),
    (simples_base.NbsNaoVinculadaException, NBS_NAO_APLICAVEL, "nbs"),
)
# O Java captura qualquer RuntimeException nas duas validações e traduz as conhecidas; o resto vira VSN-999
_FALHAS_NOMENCLATURA = (Exception,)


class _FormatoNomenclaturaInvalido(Exception):
    """ValidacaoNomenclaturaAdapter.FormatoNomenclaturaInvalidoException."""

    def __init__(self, campo):
        super().__init__(f"O {campo.upper()} deve conter exclusivamente {8 if campo == 'ncm' else 9} dígitos "
                         "ASCII, sem espaços ou símbolos.")
        self.campo = campo


_NCM_FORMATO = re.compile(r"[0-9]{8}")
_NBS_FORMATO = re.compile(r"[0-9]{9}")


def _validar_estrutural(ncm, nbs, ctx, coletor):
    # ValidacaoNomenclaturaAdapter.validarEstrutural
    try:
        _validar_ncm_nbs(ncm, nbs, ctx)
        return True
    except simples_base.BaseIndisponivel:
        raise
    except _FALHAS_NOMENCLATURA as erro:
        _registrar(erro, ncm, nbs, coletor)
        return False


def _validar_ncm_nbs(ncm, nbs, ctx):
    # ValidacaoNomenclaturaAdapter.validarNcmNbs (vazio = "" ou nulo, sem aparar)
    if ncm and nbs:
        raise simples_base.NcmNbsSimultaneasException()
    if ncm and not _NCM_FORMATO.fullmatch(ncm):
        raise _FormatoNomenclaturaInvalido("ncm")
    if nbs and not _NBS_FORMATO.fullmatch(nbs):
        raise _FormatoNomenclaturaInvalido("nbs")
    if ncm and not ctx.base.existe_ncm(ncm, ctx.data.consulta):
        raise simples_base.NcmNaoEncontradaException(ncm, ctx.data.texto)
    if nbs and not ctx.base.existe_nbs(nbs, ctx.data.consulta):
        raise simples_base.NbsNaoEncontradaException(nbs, ctx.data.texto)


def _validar_por_classificacao(ncm, nbs, ctx, classificacao, estrutura_valida, coletor):
    # ValidacaoNomenclaturaAdapter.validarPorClassificacao: só sem erro anterior e com classificação
    if not estrutura_valida or coletor.tem_erros() or classificacao is None:
        return
    try:
        if ctx.sigla_dfe is None:
            raise _FalhaTecnica("Sigla TIPO_DFE ausente para o documento habilitado na data")
        ctx.base.validar_nomenclatura(ctx.sigla_dfe, classificacao["id"], classificacao["codigo"], ncm, nbs,
                                      ctx.data.consulta, "CBS e IBS", simples_base.MODO_PERMISSIVO)
    except simples_base.BaseIndisponivel:
        raise
    except _FALHAS_NOMENCLATURA as erro:
        _registrar(erro, ncm, nbs, coletor)


def _registrar(erro, ncm, nbs, coletor):
    # ValidacaoNomenclaturaAdapter.registrar e traduzirErroConhecido
    if isinstance(erro, _FormatoNomenclaturaInvalido):
        coletor.erro(NOMENCLATURA_FORMATO_INVALIDO, erro.campo, str(erro))
        return
    for classe, codigo, campo in _CRITICAS_NOMENCLATURA:
        if isinstance(erro, classe):
            if campo == "?":
                campo = "ncm" if ncm else "nbs" if nbs else None
            coletor.erro(codigo, campo, str(erro))
            return
    coletor.falha_tecnica(_mensagem_da_excecao(erro))


# --- Alíquotas do item: o par escolhido na linha (CalculoSimplesUnificadoService.resolvedor) -------

# Nomes dos campos no contrato, os mesmos da coluna SIPA_CAMPO (EscolhaParAliquotasService)
_CAMPO_CRT = "CRT"
_CAMPO_REGAPIBSCBSSN = "regApIBSCBSSN"
_CAMPO_REGAPTRIBSN = "regApTribSN"
_CAMPO_TRIBISSQN = "tribISSQN"
_CAMPO_TPRETISSQN = "tpRetISSQN"
_CAMPO_TPBM = "tpBM"


class _SemAliquotas:
    """CalculoSimplesUnificadoService.SEM_ALIQUOTAS: regime que não calcula tributos."""

    @staticmethod
    def resolver(atividade, cbs_exigida, ibs_exigida, coletor):
        return AliquotasItem(None, None, False, None)

    @staticmethod
    def nao_avaliado():
        return None


_SEM_ALIQUOTAS = _SemAliquotas()


class _Resolvedor:
    """CalculoSimplesUnificadoService.resolvedor: campos do ISS, escolha do par e exigência das alíquotas."""

    def __init__(self, item, ctx, fatos_operacao):
        self.item = item
        self.ctx = ctx
        self.linha = ctx.linha
        self.fatos_operacao = fatos_operacao

    def resolver(self, atividade, cbs_exigida, ibs_exigida, coletor):
        c_ativ_sn = atividade.c_ativ_sn
        if atividade.atividade is None:
            return self._sem_par(c_ativ_sn, "ATIVIDADE_DESCONHECIDA")

        avisos = []
        fatos = dict(self.fatos_operacao)
        if atividade.admissivel:
            iss = _avaliar_campos_iss(self.ctx.vocabulario, atividade.atividade, self.item, coletor)
            avisos.extend(iss.avisos)
            _colocar(fatos, _CAMPO_TRIBISSQN, iss.trib_issqn)
            _colocar(fatos, _CAMPO_TPRETISSQN, iss.tp_ret_issqn)
            _colocar(fatos, _CAMPO_TPBM, iss.tp_bm)

        escolha = _escolher(atividade.atividade, atividade.admissivel, self.linha, fatos, self.ctx.situacoes_iss)
        if escolha.motivo == "FATOR_R_AUSENTE":
            coletor.erro_no_ponteiro(FATOR_R_AUSENTE, self._ponteiro_linha("fatorR", coletor),
                                     f"O fatorR é exigido: a atividade {_s(c_ativ_sn)} é sujeita ao fator \"r\" "
                                     "(Anexo III ou V).")
        if escolha.par_sem_redistribuicao_ausente:
            avisos.append(_aviso_par_sem_redistribuicao_ausente(escolha.anexo))

        par = escolha.par
        p_cbs = None if par is None else par.p_cbssn
        p_ibs = None if par is None else par.p_ibssn
        nao_calcular = False
        if escolha.caminho_par is not None:
            if self.linha.origem == "TABELA_ALIQUOTAS_EFETIVAS" and p_cbs is None:
                nao_calcular = True  # dado inconsistente na tabela do assistido: o item não é calculado
            else:
                self._exigir(cbs_exigida, p_cbs, ALIQUOTA_CBS_AUSENTE, "pCBSSN", escolha, coletor)
                self._exigir(ibs_exigida, p_ibs, ALIQUOTA_IBS_AUSENTE, "pIBSSN", escolha, coletor)

        return AliquotasItem(p_ibs, p_cbs, nao_calcular, ResolucaoItem(
            self.item.numero, c_ativ_sn, escolha.anexo, escolha.tipo_aliquota, escolha.regra, p_ibs, p_cbs,
            escolha.motivo, tuple(avisos)))

    def nao_avaliado(self):
        return self._sem_par(_normalizar_c_ativ_sn(self.item.c_ativ_sn, True), "ITEM_NAO_IMPLEMENTADO").resolucao

    def _sem_par(self, c_ativ_sn, motivo):
        return AliquotasItem(None, None, False, ResolucaoItem(
            self.item.numero, c_ativ_sn, None, None, None, None, None, motivo))

    def _exigir(self, exigida, aliquota, codigo, campo, escolha, coletor):
        # alíquota exigida e ausente no par escolhido: erro do item, com o caminho do par
        if not exigida or aliquota is not None:
            return
        par = "sem" if (escolha.tipo_aliquota == "SEM_REDISTRIBUICAO"
                        and not escolha.par_sem_redistribuicao_ausente) else "com"
        if escolha.tipo_aliquota == "PAR_UNICO":
            detalhe = (f"A alíquota {campo} do anexo {_s(escolha.anexo)} é exigida para o cálculo do item e não "
                       "está na linha de alíquotas.")
        else:
            detalhe = (f"A alíquota {campo} do anexo {_s(escolha.anexo)} (par {par} redistribuição do ISS, regra "
                       f"{_s(escolha.regra)}) é exigida para o cálculo do item e não está na linha de alíquotas.")
        coletor.erro_no_ponteiro(codigo, self._ponteiro_linha(escolha.caminho_par + "/" + campo, coletor), detalhe)

    def _ponteiro_linha(self, caminho, coletor):
        # dentro de aliquotasEfetivas quando a linha veio da requisição; no cAtivSN do item quando veio da
        # Tabela de Alíquotas Efetivas (assistido)
        if self.linha.origem == "REQUISICAO":
            return "#/" + _RAIZ_ALIQUOTAS + "/" + caminho
        return f"#/itens/{coletor.indice}/cAtivSN"


# --- Campos do ISS e escolha do par (EscolhaParAliquotasService) -------------------------------

_DOMINIO_TRIBISSQN = frozenset({1, 2, 3, 4})
_DOMINIO_TPRETISSQN = frozenset({1, 2, 3})
_DOMINIO_TPBM = frozenset({1, 2, 3, 4})
_TRIBUTAVEL = 1  # tribISSQN 1: operação tributável
_ISENCAO = 1  # tpBM 1: isenção
_RETIDO = frozenset({2, 3})  # tpRetISSQN retido pelo tomador ou pelo intermediário


@dataclass(frozen=True)
class AvaliacaoIss:
    """EscolhaParAliquotasService.AvaliacaoIssItem: campos do ISS que valem para a escolha, e avisos."""
    trib_issqn: int | None
    tp_ret_issqn: int | None
    tp_bm: int | None
    avisos: tuple


@dataclass(frozen=True)
class EscolhaPar:
    """EscolhaParAliquotasService.EscolhaParAliquotas."""
    anexo: str | None
    tipo_aliquota: str | None
    regra: str | None
    par: ParAliquotas | None
    par_sem_redistribuicao_ausente: bool
    caminho_par: str | None
    motivo: str


_PAR_ZERO = ParAliquotas(Decimal(0), Decimal(0))  # ParAliquotasSimples.ZERO


def _avaliar_campos_iss(vocabulario, atividade, item, coletor):
    # EscolhaParAliquotasService.avaliarCamposIss
    avisos = []
    trib_informado = _informado(item.trib_issqn)
    ret_informado = _informado(item.tp_ret_issqn)
    bm_informado = _informado(item.tp_bm)

    if vocabulario != "NFSE":
        desconsiderados = [campo for campo, informado in ((_CAMPO_TRIBISSQN, trib_informado),
                                                          (_CAMPO_TPRETISSQN, ret_informado),
                                                          (_CAMPO_TPBM, bm_informado)) if informado]
        if desconsiderados:
            avisos.append(f"{', '.join(desconsiderados)} desconsiderado(s): os campos do ISS só valem na NFS-e.")
        return AvaliacaoIss(None, None, None, tuple(avisos))

    iss_no_simples = atividade["issNoSimples"] is True
    c_ativ_sn = _s(atividade["cAtivSN"])

    # tribISSQN
    trib_issqn = _inteiro(item.trib_issqn)
    if trib_informado and (trib_issqn is None or trib_issqn not in _DOMINIO_TRIBISSQN):
        coletor.erro(TRIBISSQN_INVALIDO, _CAMPO_TRIBISSQN,
                     f"A tributação do ISSQN (tribISSQN) deve ser 1, 2, 3 ou 4, recebido: '{item.trib_issqn}'.")
        trib_issqn = None
    elif not trib_informado and iss_no_simples:
        coletor.erro(TRIBISSQN_INVALIDO, _CAMPO_TRIBISSQN,
                     f"A tributação do ISSQN (tribISSQN) é obrigatória para a atividade {c_ativ_sn}, que tem ISS "
                     "no Simples Nacional.")

    # tpRetISSQN
    tp_ret_issqn = None
    if ret_informado and trib_issqn is not None and trib_issqn != _TRIBUTAVEL:
        avisos.append(f"tpRetISSQN desconsiderado: não há retenção do ISS com tribISSQN {trib_issqn} (a NFS-e "
                      "recusaria a combinação: regra E0580).")
    elif ret_informado:
        tp_ret_issqn = _inteiro(item.tp_ret_issqn)
        if tp_ret_issqn is None or tp_ret_issqn not in _DOMINIO_TPRETISSQN:
            coletor.erro(TPRETISSQN_INVALIDO, _CAMPO_TPRETISSQN,
                         "O tipo de retenção do ISSQN (tpRetISSQN) deve ser 1, 2 ou 3, recebido: "
                         f"'{item.tp_ret_issqn}'.")
            tp_ret_issqn = None
    elif iss_no_simples and trib_issqn == _TRIBUTAVEL:
        coletor.erro(TPRETISSQN_INVALIDO, _CAMPO_TPRETISSQN,
                     f"O tipo de retenção do ISSQN (tpRetISSQN) é obrigatório com tribISSQN = 1 na atividade "
                     f"{c_ativ_sn}, que tem ISS no Simples Nacional.")

    # tpBM
    tp_bm = None
    if bm_informado and atividade["admiteBeneficioMunicipal"] is not True:
        avisos.append(f"tpBM desconsiderado: a atividade {c_ativ_sn} não admite benefício municipal do ISS "
                      "(regra E0536 da NFS-e: só os subitens 7.02 e 7.05, atividades 12 e 13).")
    elif bm_informado:
        tp_bm = _inteiro(item.tp_bm)
        if tp_bm is None or tp_bm not in _DOMINIO_TPBM:
            coletor.erro(TPBM_INVALIDO, _CAMPO_TPBM,
                         f"O tipo de benefício municipal (tpBM) deve ser 1, 2, 3 ou 4, recebido: '{item.tp_bm}'.")
            tp_bm = None

    if tp_bm == _ISENCAO and tp_ret_issqn is not None and tp_ret_issqn in _RETIDO:
        avisos.append(f"tpBM 1 (isenção) com tpRetISSQN {tp_ret_issqn}: combinação recusada pela NFS-e (regra "
                      "E0594); vale a isenção municipal (par sem redistribuição).")
    return AvaliacaoIss(trib_issqn, tp_ret_issqn, tp_bm, tuple(avisos))


def _escolher(atividade, admissivel, linha, fatos, situacoes):
    # EscolhaParAliquotasService.escolher: a primeira regra que se aplica decide
    # Regra 1: DOCUMENTO.
    if not admissivel:
        return EscolhaPar(None, None, "DOCUMENTO", None, False, None, "ATIVIDADE_INADMISSIVEL_NO_DOCUMENTO")

    # Regra 2: ATIVIDADE_NAO_TRIBUTADA, atividade sem anexo (código 90).
    anexo_com_fator_r = atividade["anexoComFatorR"]
    anexo_sem_fator_r = atividade["anexoSemFatorR"]
    if anexo_com_fator_r is None and anexo_sem_fator_r is None:
        return EscolhaPar(None, "SEM_ALIQUOTA", "ATIVIDADE_NAO_TRIBUTADA", _PAR_ZERO, False, None,
                          "ATIVIDADE_SEM_ANEXO")

    # Anexo: "sem fator r", ou "com fator r" na atividade sujeita ao fator r.
    anexo = anexo_sem_fator_r
    if anexo_com_fator_r is not None:
        if linha.fator_r is None:
            return EscolhaPar(None, None, None, None, False, None, "FATOR_R_AUSENTE")
        anexo = anexo_com_fator_r if linha.fator_r else anexo_sem_fator_r
    aliquotas_anexo = linha.anexo(anexo)
    par_do_anexo = None if aliquotas_anexo is None else aliquotas_anexo.par
    caminho_anexo = "anexo" + anexo

    # Regra 3: ANEXO_SEM_ISS, anexos I e II têm um só par.
    if anexo not in ("III", "IV", "V"):
        return _escolhido(anexo, "PAR_UNICO", "ANEXO_SEM_ISS", par_do_anexo, False, caminho_anexo)

    # Regras 4 a 9: anexos III, IV e V.
    regra, tipo = "PADRAO", "COM_REDISTRIBUICAO"
    if linha.impedimento_sublimite:
        # Regra 4: o impedimento vem da linha de alíquotas.
        regra, tipo = "ISS_FORA_DO_SIMPLES", "SEM_REDISTRIBUICAO"
    elif (situacao := _situacao("ISS_FORA_DO_SIMPLES", fatos, situacoes)) is not None:
        regra, tipo = situacao["regra"], situacao["tipoAliquota"]
    elif atividade["issNoSimples"] is not True:
        # Regra 5: atividade sem ISS no Simples.
        regra, tipo = "ATIVIDADE_SEM_ISS", "SEM_REDISTRIBUICAO"
    else:
        # Regras 6, 7 e 8, nesta ordem.
        for candidata in ("ISS_NAO_INCIDE", "ISENCAO_MUNICIPAL", "ISS_RETIDO"):
            situacao = _situacao(candidata, fatos, situacoes)
            if situacao is not None:
                regra, tipo = situacao["regra"], situacao["tipoAliquota"]
                break

    if tipo != "SEM_REDISTRIBUICAO":
        return _escolhido(anexo, tipo, regra, par_do_anexo, False, caminho_anexo)
    # Par sem redistribuição nulo: vale o par com.
    sem = None if aliquotas_anexo is None else aliquotas_anexo.sem_redistribuicao_iss
    if sem is None:
        return _escolhido(anexo, tipo, regra, par_do_anexo, True, caminho_anexo)
    return _escolhido(anexo, tipo, regra, sem, False, caminho_anexo + "/semRedistribuicaoISS")


def _aviso_par_sem_redistribuicao_ausente(anexo):
    # EscolhaParAliquotasService.avisoParSemRedistribuicaoAusente
    return (f"Par sem redistribuição do ISS ausente no anexo {_s(anexo)}; aplicado o par com redistribuição, "
            "igual quando o ISS não passa do teto.")


def _escolhido(anexo, tipo, regra, par, sem_ausente, caminho):
    # EscolhaParAliquotasService.escolhido
    motivo = "ANEXO_SEM_ALIQUOTA" if par is None or par.p_cbssn is None else "RESOLVIDA"
    return EscolhaPar(anexo, tipo, regra, par, sem_ausente, caminho, motivo)


def _situacao(regra, fatos, situacoes):
    # EscolhaParAliquotasService.situacao: a primeira situação vigente da regra com o valor do documento
    for situacao in situacoes:
        if situacao["regra"] == regra and situacao["valor"] == fatos.get(situacao["campo"]):
            return situacao
    return None


# --- A conta (SimplesNacionalCalculoService) -------------------------------------------------

def _calcular_item(regime, tipo_receita, motivo_nao_calculo, base_calculo, valor_is, p_ibssn, p_cbssn,
                   tp_ente_gov, fator_transicao, casas_aliquota):
    # SimplesNacionalCalculoService.calcularItem: devolve (estado, motivo, Tributos)
    # Receita bruta do item: zero quando o tipo de receita não compõe receita.
    if tipo_receita["compoeReceita"] is True:
        parcelas = [base_calculo]
        if tipo_receita["impostoSeletivoCompoeReceita"] is True and valor_is is not None:
            parcelas.append(valor_is)
        v_rbsn_item = numeros.arredondar_valor(numeros.totalizar(parcelas))
    else:
        v_rbsn_item = Decimal(0)

    # Regime que não calcula tributos ou item não calculável: só a receita bruta. O regime vem antes.
    motivo = (_motivo("REGIME_SOMENTE_RECEITA") if regime["calculaTributos"] is False else motivo_nao_calculo)
    if motivo is not None:
        return NAO_CALCULADO, motivo, Tributos(v_rbsn_item, tipo_receita["codigo"], tipo_receita["descricao"])

    # Transformação das alíquotas: redutor (não aplicado), deslocamento da compra governamental, leiaute.
    p_ibssn, p_cbssn = _arredondar_aliquotas_do_leiaute(
        *_aplicar_deslocamento_cbs_ibs(p_ibssn, p_cbssn, tp_ente_gov, fator_transicao), casas_aliquota)

    fator_receita = tipo_receita["fatorReceita"]
    pendente_suspenso = tipo_receita["pendenteSuspenso"] is True
    v_bc = numeros.arredondar_valor(numeros.multiplicar(v_rbsn_item, fator_receita))
    valor_ibs = numeros.arredondar_valor(_valor_tributo(v_rbsn_item, fator_receita, p_ibssn))
    valor_cbs = numeros.arredondar_valor(_valor_tributo(v_rbsn_item, fator_receita, p_cbssn))

    # Regime que não gera IBS (CRT=2): os campos do IBS não saem.
    gera_ibs = regime["geraIbs"] is True
    ibs = _repartir_por_regime(regime, valor_ibs, pendente_suspenso) if gera_ibs else None
    cbs = _repartir_por_regime(regime, valor_cbs, pendente_suspenso)

    # Total do item: só os valores devidos, mais o IS repassado.
    total_item = numeros.totalizar(v for v in (None if ibs is None else ibs[0], cbs[0], valor_is) if v is not None)

    return CALCULADO, None, Tributos(
        v_rbsn_item=v_rbsn_item, tp_rbsn=tipo_receita["codigo"], tp_rbsn_descricao=tipo_receita["descricao"],
        v_bc=v_bc, p_ibssn=p_ibssn if gera_ibs else None, v_ibssn=None if ibs is None else ibs[0],
        v_ibs_pend_susp=None if ibs is None else ibs[1], p_cbssn=p_cbssn, v_cbssn=cbs[0], v_cbs_pend_susp=cbs[1],
        imposto_seletivo=valor_is, total_item=total_item,
        memoria_cbs=_memoria_calculo(tipo_receita, v_rbsn_item, v_bc, p_cbssn, cbs),
        memoria_ibs=None if ibs is None else _memoria_calculo(tipo_receita, v_rbsn_item, v_bc, p_ibssn, ibs))


def _memoria_calculo(tipo_receita, v_rbsn_item, v_bc, aliquota, valor):
    # SimplesNacionalCalculoService.memoriaCalculo
    return (f"Receita bruta do Simples Nacional de R$ {_moeda_br(v_rbsn_item)} ({_s(tipo_receita['descricao'])}). "
            f"A base de cálculo utilizada é de R$ {_moeda_br(v_bc)}, com alíquota efetiva de "
            f"{_percentual_br(aliquota)}%. Valor devido de R$ {_moeda_br(valor[0])} e pendente/suspenso de "
            f"R$ {_moeda_br(valor[1])}.")


def _aplicar_deslocamento_cbs_ibs(p_ibssn, p_cbssn, tp_ente_gov, fator):
    # SimplesNacionalCalculoService.aplicarDeslocamentoCbsIbs (o redutor do passo 1 é identidade)
    if tp_ente_gov is None or (p_ibssn is None and p_cbssn is None):
        return p_ibssn, p_cbssn
    p_ibs = Decimal(0) if p_ibssn is None else p_ibssn
    p_cbs = Decimal(0) if p_cbssn is None else p_cbssn
    if tp_ente_gov == 1:
        # União: pCBS' = pCBS + pIBS; pIBS' = 0.
        return Decimal(0), numeros.totalizar((p_cbs, p_ibs))
    if fator is None:
        raise _FalhaTecnica(f"Fator de transição de compra governamental não resolvido para tpEnteGov {tp_ente_gov}")
    # pIBS' = pIBS + f x pCBS; pCBS' = (1 - f) x pCBS.
    return (numeros.totalizar((p_ibs, numeros.multiplicar(fator, p_cbs))),
            numeros.multiplicar(numeros.subtrair(Decimal(1), fator), p_cbs))


def _arredondar_aliquotas_do_leiaute(p_ibssn, p_cbssn, casas):
    # SimplesNacionalCalculoService.arredondarAliquotasDoLeiaute (NFS-e: 2 casas; None = precisão integral)
    if casas is None:
        return p_ibssn, p_cbssn
    return numeros.arredondar_aliquota(p_ibssn, casas), numeros.arredondar_aliquota(p_cbssn, casas)


def _valor_tributo(v_rbsn_item, fator_receita, aliquota):
    # SimplesNacionalCalculoService.valorTributo: exato, sem arredondamento
    if aliquota is None or fator_receita.is_zero():
        return Decimal(0)
    return numeros.multiplicar(numeros.multiplicar(v_rbsn_item, fator_receita),
                               numeros.percentual_para_fracao(aliquota))


def _repartir_por_regime(regime, valor, pendente_suspenso):
    # SimplesNacionalCalculoService.repartirPorRegime: (devido, pendente/suspenso)
    destino = regime["destinoPendenteSuspenso"] if pendente_suspenso else regime["destinoDevido"]
    if destino == "DEVIDO":
        return valor, Decimal(0)
    if destino == "PENDENTE_SUSPENSO":
        return Decimal(0), valor
    if destino == "ZERO":
        return Decimal(0), Decimal(0)
    raise _FalhaTecnica(f"destino de valor desconhecido: {destino}")  # no Java, exceção dentro do try do item


# --- Total (TotalSimplesDomain.totalizar) -----------------------------------------------------

def _totalizar(objetos):
    # TotalSimplesDomain.totalizar e create: só os itens com tribCalc; None quando não há nenhum
    tributos = [o.trib_calc for o in objetos if o.trib_calc is not None]
    if not tributos:
        return None

    def somar(campo):
        valores = [getattr(t, campo) for t in tributos if getattr(t, campo) is not None]
        return numeros.totalizar(valores) if valores else None

    v_ibs, v_cbs, total_is = somar("v_ibssn"), somar("v_cbssn"), somar("imposto_seletivo")
    total_tributos = None
    if v_ibs is not None or v_cbs is not None or total_is is not None:
        total_tributos = numeros.totalizar(v for v in (v_ibs, v_cbs, total_is) if v is not None)
    return _sem_nulos({
        "vRBSNtot": _moeda(somar("v_rbsn_item")),
        "vBC": _moeda(somar("v_bc")),
        "vIBSSNtot": _moeda(v_ibs),
        "vIBSSNtotPendSusp": _moeda(somar("v_ibs_pend_susp")),
        "vCBSSNtot": _moeda(v_cbs),
        "vCBSSNtotPendSusp": _moeda(somar("v_cbs_pend_susp")),
        "totalIs": _moeda(total_is),
        "totalTributos": _moeda(total_tributos),
    })


# --- Saída: o JSON como o Jackson escreve ------------------------------------------------------
# NON_NULL omite os nulos (oper, objetos, tribCalc, total, erros); ALWAYS os mantém (resolucaoAliquotas,
# itens da resolução, motivoEstado). Valores fiscais com BigDecimalTDec1302Serializer (2 casas, texto),
# alíquotas do cálculo com BigDecimalTDec0302_04Serializer (2 a 4 casas, texto) e alíquotas da resolução
# com BigDecimalTextoIntegralSerializer (toPlainString, texto).

def _sem_nulos(campos):
    return {chave: valor for chave, valor in campos.items() if valor is not None}


def _moeda(valor):
    # BigDecimalTDec1302Serializer
    return None if valor is None else numeros.formatar_moeda(valor)


def _aliquota_publicada(valor):
    # BigDecimalTDec0302_04Serializer
    return None if valor is None else numeros.formatar_aliquota(valor)


def _texto_integral(valor):
    # BigDecimalTextoIntegralSerializer: BigDecimal.toPlainString
    return None if valor is None else format(valor, "f")


def _no_padrao_brasileiro(texto):
    # DecimalFormatSymbols pt-BR: milhar com ponto, decimal com vírgula ("1234.5" vira "1.234,5")
    inteiro, _, fracao = texto.partition(".")
    sinal = "-" if inteiro.startswith("-") else ""
    inteiro = inteiro.lstrip("-")
    grupos = []
    while len(inteiro) > 3:
        grupos.insert(0, inteiro[-3:])
        inteiro = inteiro[:-3]
    grupos.insert(0, inteiro)
    return sinal + ".".join(grupos) + ("," + fracao if fracao else "")


def _moeda_br(valor):
    # SimplesNacionalCalculoService.formatarMoeda: DecimalFormat("#,##0.00") pt-BR, HALF_EVEN; nulo vale zero
    return _no_padrao_brasileiro(numeros.formatar_moeda(Decimal(0) if valor is None else valor))


def _percentual_br(valor):
    # SimplesNacionalCalculoService.formatarPercentual: DecimalFormat("#,##0.00##") pt-BR; nulo vale zero
    return _no_padrao_brasileiro(numeros.formatar_aliquota(Decimal(0) if valor is None else valor))


def _operacao_json(dados, regime_apuracao):
    # OperacaoSimplesDomain: os campos como chegaram, e o código neutro do regime
    return _sem_nulos({
        "data": dados.data, "tpDoc": dados.tp_doc, "CRT": dados.crt, "tpRegimeIBSCBS": dados.tp_regime_ibscbs,
        "opSimpNac": dados.op_simp_nac, "regApIBSCBSSN": dados.reg_ap_ibscbssn,
        "regApTribSN": dados.reg_ap_tribsn, "tpEnteGov": dados.tp_ente_gov, "regimeApuracao": regime_apuracao,
    })


def _resolucao_json(linha, itens, avisos):
    # ResolucaoAliquotasNaoAssistidoDomain (ALWAYS): fatorR pode sair null
    return {"fatorR": linha.fator_r, "indSublimite": linha.impedimento_sublimite,
            "itens": [_resolucao_item_json(r) for r in itens], "avisos": list(avisos)}


def _resolucao_item_json(resolucao):
    # ResolucaoAliquotaItemDomain (ALWAYS): o que não foi resolvido sai null
    if resolucao is None:
        return None
    return {
        "numero": resolucao.numero, "cAtivSN": resolucao.c_ativ_sn, "anexoAplicado": resolucao.anexo,
        "tipoAliquota": resolucao.tipo_aliquota, "regraAliquota": resolucao.regra,
        "pIBSSN": _texto_integral(resolucao.p_ibssn), "pCBSSN": _texto_integral(resolucao.p_cbssn),
        "motivo": resolucao.motivo, "avisos": list(resolucao.avisos),
    }


def _objeto_json(objeto):
    # ObjetoSimplesDomain (NON_NULL)
    return _sem_nulos({
        "nObj": objeto.n_obj, "estadoItem": objeto.estado, "motivoEstado": objeto.motivo,
        "tribCalc": None if objeto.trib_calc is None else _tributos_json(objeto.trib_calc),
        "erros": objeto.erros,
    })


def _tributos_json(t):
    # TributosSimplesDomain (NON_NULL), na ordem do @JsonPropertyOrder
    memoria = None
    if t.memoria_cbs is not None or t.memoria_ibs is not None:
        memoria = _sem_nulos({"cbs": t.memoria_cbs, "ibs": t.memoria_ibs})
    return _sem_nulos({
        "vRBSNitem": _moeda(t.v_rbsn_item), "tpRBSN": t.tp_rbsn, "tpRBSNDescricao": t.tp_rbsn_descricao,
        "vBC": _moeda(t.v_bc), "pIBSSN": _aliquota_publicada(t.p_ibssn), "vIBSSN": _moeda(t.v_ibssn),
        "vIBSPendSusp": _moeda(t.v_ibs_pend_susp), "pCBSSN": _aliquota_publicada(t.p_cbssn),
        "vCBSSN": _moeda(t.v_cbssn), "vCBSPendSusp": _moeda(t.v_cbs_pend_susp),
        "IS": None if t.imposto_seletivo is None else {"valor": _moeda(t.imposto_seletivo)},
        "totalItem": _moeda(t.total_item), "memoriaCalculo": memoria,
    })


def calcular(operacao, base, base_url="", *, instancia=None, agora=None):
    """SimplesNacionalController.calcularTributosSimplesNacionalNaoAssistido.

    `operacao` é o corpo JSON (dict, lido com decimal.Decimal nos números). Devolve (status, corpo) como a
    oficial: 422 com a lista de erros globais, 400 para corpo fora do contrato, 500 para falha da
    oficial; com a operação válida, 200 com os itens calculados.

    `base_url` é esquema://host da requisição (HttpUtils.getBaseURL, sem porta), que entra no `type` dos
    erros. `instancia` é o caminho pedido, com o /api do perfil offline (padrão: o do endpoint não
    assistido). `agora` é o instante do `timestamp` do erro (padrão: o relógio).
    """
    instancia = CAMINHO_NAO_ASSISTIDO if instancia is None else instancia
    try:
        dados = _ler_corpo(operacao)
        contexto = _preparar_operacao(dados, base, base_url, instancia, agora)
        return _processar_itens(contexto)
    except _ErrosCalculo as erro:
        return _corpo_422(erro.erros, instancia, agora)
    except _CorpoIlegivel as erro:
        return _corpo_problema(400, "Mensagem incompreensível", "mensagem-incompreensivel",
                               erro.detalhe, base_url, instancia, agora)
    except _ErroSistema as erro:
        return _corpo_problema(500, "Erro de sistema não previsto", "erro-de-sistema-nao-previsto",
                               erro.detalhe, base_url, instancia, agora)
