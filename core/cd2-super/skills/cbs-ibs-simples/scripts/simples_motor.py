"""Motor do cálculo da skill cbs-ibs-simples: a operação inteira.

Port de CalculoSimplesUnificadoService (calculadora-rtc-simples-nacional, tag 1.0.2), na parte que vale
para a operação como um todo, e de SimplesNacionalNaoAssistidoService (a linha de alíquotas vem no
corpo). Também traz o catálogo de erros VSN- (CodigoValidacaoSimples) e o tratamento de erro da API
(shared/exceptionhandler). O cálculo de cada item é da Tarefa 6, no mesmo arquivo.

Como a oficial, o motor não deduz, não corrige e não completa a entrada. Erro de validação sai na ordem
em que o Java o produz. Mensagens, títulos e detalhes são os do Java, letra por letra; onde o Java monta
a mensagem com `String.format`, o formato é o mesmo, com `%s` e `%d` trocados pelo valor. Só biblioteca
padrão, Decimal para todo número.

Fluxo de `calcular`, com o que cada passo guarda no `ContextoOperacao` que a Tarefa 6 recebe:
  1. `_ler_corpo`: o JSON vira `DadosOperacao` (texto, como o Jackson entrega). Estrutura fora do
     contrato é HTTP 400 (HttpMessageNotReadableException).
  2. validação global (HTTP 422): campos globais, documento, regime, quantidade de itens.
  3. regime de apuração, fator de transição de compra governamental, linha de alíquotas, avisos.
  4. `_processar_itens(contexto)`: o ponto onde a Tarefa 6 entra.
"""
from __future__ import annotations

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

import simples_base  # noqa: E402  (precisa do caminho acima)

CAMINHO_NAO_ASSISTIDO = "/api/calculadora/observabilidade/simples-nacional/nao-assistido"  # com o /api do perfil offline
CAMINHO_ERROS = "/calculadora/observabilidade/simples-nacional/erros"

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
        "timestamp": simples_base._instante(agora),
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
        "timestamp": simples_base._instante(agora),
    }


# --- Leitura do corpo (o que o Jackson faz com OperacaoSimplesNaoAssistidaInput) -----------------
# Campos de texto aceitam número e booleano (coerção de escalar); objeto e lista no lugar de texto, lista
# ou registro são HTTP 400, com a mensagem do Jackson. Campo desconhecido é ignorado. O que o JSON traz
# dentro de cada item de `itens` fica como veio: o formato do item é da Tarefa 6.

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


def _sem_construtor(tipo, valor):
    # registro recebendo escalar: o Jackson tenta um construtor de um argumento
    if isinstance(valor, str):
        como = f"no String-argument constructor/factory method to deserialize from String value ('{valor}')"
    elif isinstance(valor, bool):
        como = ("no boolean/Boolean-argument constructor/factory method to deserialize from boolean value "
                f"({'true' if valor else 'false'})")
    elif isinstance(valor, int):
        como = f"no int/Int-argument constructor/factory method to deserialize from Number value ({valor})"
    else:
        como = f"no double/Double-argument constructor/factory method to deserialize from Number value ({valor})"
    return f"Cannot construct instance of `{tipo}` (although at least one Creator exists): {como}"


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
        texto = _aparar(valor).lower()
        if texto in ("true", "false"):
            return texto == "true"
        raise _CorpoIlegivel('JSON parse error: Cannot deserialize value of type `java.lang.Boolean` from '
                             f'String "{valor}": only "true" or "false" recognized')
    raise _CorpoIlegivel("JSON parse error: " + _de_valor(_T_BOOLEAN, valor))


def _ler_registro(tipo, no):
    """Lê um registro do contrato: dict com todos os campos do tipo (None onde faltou)."""
    if no is None:
        return None
    if not isinstance(no, dict):
        if isinstance(no, list):
            raise _CorpoIlegivel("JSON parse error: " + _de_valor(tipo, no))
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
    # Ponto de extensão da Tarefa 6: a leitura de cada item (ItemSimplesUnificadoInput) entra aqui.
    return None if lista is None else list(lista)


@dataclass(frozen=True)
class DadosOperacao:
    """CalculoSimplesUnificadoService.DadosOperacaoSimples: a operação como chegou (texto ou None).

    `itens` é a lista como veio do JSON (cada item ainda um dict, ou None), ou None se ausente.
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

    `consulta` é a data usada nas consultas à base (anos fora de 1 a 9999 ficam presos nas pontas, o que
    dá a mesma vigência); `texto` é o LocalDate.toString do Java (o que vai nas mensagens).
    `ano` é o ano de verdade: acima de 9999 a oficial falha ao consultar a base (HTTP 500).
    """
    consulta: date
    texto: str
    ano: int


def _parse_data(valor):
    # LocalDate.parse(valor.trim()); None quando o Java lança DateTimeParseException
    achou = _DATA_ISO.fullmatch(_aparar(valor))
    if achou is None:
        return None
    sinal, digitos, mes, dia = achou.groups()
    if len(digitos) > 4 and sinal != "+":  # SignStyle.EXCEEDS_PAD
        return None
    if len(digitos) == 4 and sinal == "+":
        return None
    ano = int(digitos) * (-1 if sinal == "-" else 1)
    if abs(ano) > 999_999_999:
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
    consulta = date(max(1, min(ano, 9999)), mes, dia) if 1 <= ano <= 9999 else (
        date.min if ano < 1 else date(9999, 12, 31))
    return DataOperacao(consulta, texto, ano)


# --- Contexto que a Tarefa 6 recebe --------------------------------------------------------------

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

    É a interface da Tarefa 6 (CalculoSimplesUnificadoService.processar, passos 5 e 6):
      dados          DadosOperacao, a operação como chegou (itens ainda crus)
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
        avisos.append("regApIBSCBSSN 2 (excesso de sublimite) com regApTribSN 1: incoerente — quem excede "
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


def _processar_itens(contexto):
    # CalculoSimplesUnificadoService.processar, passos 5 e 6 (itens e saída): Tarefa 6.
    # Recebe o ContextoOperacao e devolve (200, corpo). Erro de item sobe como _ErrosCalculo.
    raise NotImplementedError("itens: Tarefa 6")


def calcular(operacao, base, base_url="", *, instancia=None, agora=None):
    """SimplesNacionalController.calcularTributosSimplesNacionalNaoAssistido.

    `operacao` é o corpo JSON (dict, lido com decimal.Decimal nos números). Devolve (status, corpo) como a
    oficial: 422 com a lista de erros globais, 400 para corpo fora do contrato, 500 para falha da
    oficial; com a operação válida, segue para os itens (Tarefa 6).

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
