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

"""Erros da skill cbs-ibs-regime-regular: o catálogo da calculadora oficial e as respostas de erro.

Port (calculadora-rtc-regime-regular, tag 1.7.2) de:
- api/exceptionhandler/enumeration/ProblemType.java: os códigos REG-001 a REG-038 (sem REG-017), CAL-001 a
  CAL-012 e CAL-999, com título, caminho do `type`, campo padrão do ponteiro, estado do item e descrição;
- api/exceptionhandler/enumeration/CodigoValidacao.java: os códigos VAL-000 a VAL-015 do Bean Validation;
- domain/service/ErroService.java: `descrever_erro` (GET /calculadora/observabilidade/erros?codigo=);
- api/exceptionhandler/ApiExceptionHandler.java e ObservabilidadeExceptionHandler.java: o corpo das
  respostas de erro (`corpo_problema`, `corpo_campos_invalidos`, `corpo_erros_calculo`);
- domain/service/collector/ItemCollector.java: o detalhe de erro de um item (`detalhe_de_item`).

O status de `/regime-geral` segue a hierarquia das exceções Java (ApiExceptionHandler): ValidacaoException
422, EntidadeNaoEncontradaException 404, CampoInvalidoException (e a DataFatoGeradorNaoInformadaException,
filha dela) 400, o resto 500. Bean Validation é 400 "Campos inválidos." e JSON ilegível é 400 com o título
de erro de sistema. A ordem das chaves é a que a oficial escreve (Jackson 3 ordena as do ProblemDetail).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

# EstadoItemEnum
CALCULADO = "CALCULADO"
NAO_IMPLEMENTADO = "NAO_IMPLEMENTADO"
INCONSISTENCIA_ENTRADA = "INCONSISTENCIA_ENTRADA"
FALHA_TECNICA = "FALHA_TECNICA"
CALCULO_SIMULADO = "CALCULO_SIMULADO"

# Status pela superclasse da exceção Java (ApiExceptionHandler)
_VALIDACAO, _NAO_ENCONTRADA, _CAMPO_INVALIDO, _SISTEMA = 422, 404, 400, 500

CAMINHO_ERROS = "/calculadora/observabilidade/erros"
TITULO_SISTEMA = "Erro de sistema não previsto"
CAMINHO_SISTEMA = "erro-de-sistema-nao-previsto"

# ProblemType com código: codigo -> (título, caminho do type, campo padrão, estado, status, descrição)
_PROBLEMAS = {
    "REG-024": ("Campo inválido", "campo-invalido", None, INCONSISTENCIA_ENTRADA, _CAMPO_INVALIDO,
                "O valor informado para o campo não é válido conforme as regras de negócio."),
    "REG-001": ("NCM e NBS informadas simultaneamente", "ncm-nbs-simultaneas", None, INCONSISTENCIA_ENTRADA,
                _VALIDACAO, "Apenas uma nomenclatura (NCM ou NBS) deve ser informada por item. A presença "
                "simultânea impede a classificação."),
    "REG-002": ("NCM não encontrada", "ncm-nao-encontrada", "ncm", INCONSISTENCIA_ENTRADA, _NAO_ENCONTRADA,
                "O código NCM informado não foi localizado na base de nomenclaturas vigente para a data do fato "
                "gerador."),
    "REG-003": ("NBS não encontrada", "nbs-nao-encontrada", "nbs", INCONSISTENCIA_ENTRADA, _NAO_ENCONTRADA,
                "O código NBS informado não foi localizado na base de nomenclaturas vigente para a data do fato "
                "gerador."),
    "REG-004": ("Situação tributária não encontrada", "situacao-tributaria-nao-encontrada", "cst",
                INCONSISTENCIA_ENTRADA, _NAO_ENCONTRADA,
                "A situação tributária (CST) informada não existe na base de dados para o tributo e data do fato "
                "gerador."),
    "REG-005": ("Classificação tributária não encontrada", "classificacao-tributaria-nao-encontrada",
                "cClassTrib", INCONSISTENCIA_ENTRADA, _NAO_ENCONTRADA,
                "A classificação tributária informada não foi localizada na base vigente para a data do fato "
                "gerador."),
    "REG-006": ("Classificação tributária não vinculada à situação tributária",
                "classificacao-tributaria-nao-vinculada-situacao-tributaria", "cClassTrib", INCONSISTENCIA_ENTRADA,
                _VALIDACAO, "A classificação tributária informada não está vinculada à situação tributária "
                "selecionada."),
    "REG-007": ("Erro de nomenclatura", "erro-nomenclatura", "ncm", INCONSISTENCIA_ENTRADA, _VALIDACAO,
                "A nomenclatura informada apresenta inconsistência com as regras de classificação tributária "
                "vigentes."),
    "REG-008": ("Tratamento de classificação não encontrado", "tratamento-classificacao-nao-encontrado",
                "cClassTrib", INCONSISTENCIA_ENTRADA, _NAO_ENCONTRADA,
                "Não foi encontrado tratamento de classificação tributária para os parâmetros informados."),
    "REG-009": ("Grupo de tributação regular não informado", "grupo-tributacao-regular-nao-informado",
                "tributacaoRegular", INCONSISTENCIA_ENTRADA, _VALIDACAO,
                "O grupo de tributação regular é obrigatório para a situação tributária selecionada e não foi "
                "informado."),
    "REG-010": ("Incompatibilidade com suspensão", "incompatibilidade-suspensao", "tributacaoRegular/cClassTrib",
                INCONSISTENCIA_ENTRADA, _VALIDACAO,
                "A classificação tributária informada é incompatível com a situação de suspensão aplicada ao "
                "item."),
    "REG-011": ("NCM não vinculada", "ncm-nao-vinculada", "ncm", INCONSISTENCIA_ENTRADA, _VALIDACAO,
                "O código NCM informado não está vinculado a nenhuma classificação tributária vigente."),
    "REG-012": ("NBS não vinculada", "nbs-nao-vinculada", "nbs", INCONSISTENCIA_ENTRADA, _VALIDACAO,
                "O código NBS informado não está vinculado a nenhuma classificação tributária vigente."),
    "REG-013": ("Dados do Imposto Seletivo não informados", "dados-imposto-seletivo-nao-informados",
                "impostoSeletivo", INCONSISTENCIA_ENTRADA, _VALIDACAO,
                "Os dados do Imposto Seletivo são obrigatórios para a classificação tributária selecionada e não "
                "foram informados."),
    "REG-014": ("Dados do Imposto Seletivo informados indevidamente",
                "dados-imposto-seletivo-informados-indevidamente", "impostoSeletivo", INCONSISTENCIA_ENTRADA,
                _VALIDACAO, "Os dados do Imposto Seletivo foram informados, mas a classificação tributária "
                "selecionada não prevê incidência."),
    "REG-015": ("NCM completo não informado", "ncm-completo-nao-informado", "ncm", INCONSISTENCIA_ENTRADA,
                _VALIDACAO, "O código NCM deve ser informado com todos os dígitos (completo) para a operação "
                "solicitada."),
    "REG-016": ("NBS completo não informado", "nbs-completo-nao-informado", "nbs", INCONSISTENCIA_ENTRADA,
                _VALIDACAO, "O código NBS deve ser informado com todos os dígitos (completo) para a operação "
                "solicitada."),
    "REG-018": ("Município não pertencente à UF", "municipio-nao-pertencente-uf", None, INCONSISTENCIA_ENTRADA,
                _VALIDACAO, "O município informado não pertence à UF indicada na operação."),
    "REG-019": ("Município não encontrado", "municipio-nao-encontrado", None, INCONSISTENCIA_ENTRADA,
                _NAO_ENCONTRADA, "O código do município informado não foi localizado na base de dados."),
    "REG-020": ("UF não encontrada", "uf-nao-encontrada", None, INCONSISTENCIA_ENTRADA, _NAO_ENCONTRADA,
                "A UF informada para a operação não foi encontrada na base de dados."),
    "REG-021": ("Data do fato gerador não informada", "fato-gerador-nao-informada", None, INCONSISTENCIA_ENTRADA,
                _CAMPO_INVALIDO, "A data do fato gerador é obrigatória e não foi informada na requisição."),
    "REG-022": ("Grupo de tributação regular informado indevidamente",
                "grupo-tributacao-regular-informado-indevidamente", "tributacaoRegular", INCONSISTENCIA_ENTRADA,
                _VALIDACAO, "O grupo de tributação regular foi informado, mas a situação tributária selecionada "
                "não o exige."),
    "REG-023": ("Erro genérico de validação", "erro-generico-validacao", None, INCONSISTENCIA_ENTRADA, _VALIDACAO,
                "Erro de validação que não se enquadra em nenhuma categoria específica."),
    "REG-025": ("Base de cálculo não informada", "base-calculo-nao-informada", "baseCalculo",
                INCONSISTENCIA_ENTRADA, _VALIDACAO,
                "A base de cálculo de CBS e IBS é obrigatória quando o grupo do Imposto Seletivo não é "
                "informado."),
    "REG-026": ("Base de cálculo inconsistente", "base-calculo-inconsistente", "baseCalculo",
                INCONSISTENCIA_ENTRADA, _VALIDACAO,
                "A base de cálculo de CBS e IBS informada difere da soma da base de cálculo do Imposto Seletivo "
                "com o Imposto Seletivo informado."),
    "REG-027": ("Base de cálculo menor que a base de cálculo do Imposto Seletivo",
                "base-calculo-menor-base-calculo-imposto-seletivo", "baseCalculo", INCONSISTENCIA_ENTRADA,
                _VALIDACAO, "A base de cálculo de CBS e IBS informada é menor que a base de cálculo do Imposto "
                "Seletivo."),
    "REG-028": ("Tipo de documento fiscal não encontrado", "tipo-documento-fiscal-nao-encontrado", "tpDoc",
                INCONSISTENCIA_ENTRADA, _VALIDACAO,
                "O tipo de documento fiscal (tpDoc) informado na operação não existe ou está fora de vigência na "
                "data do fato gerador."),
    "REG-029": ("Classificação tributária não vinculada ao tipo de documento",
                "classificacao-tributaria-nao-vinculada-tipo-documento", "cClassTrib", INCONSISTENCIA_ENTRADA,
                _VALIDACAO, "A classificação tributária informada não possui vínculo vigente com o tipo de "
                "documento fiscal (tpDoc) da operação."),
    "REG-030": ("Nomenclatura incompatível com o tipo de documento", "nomenclatura-incompativel-tipo-documento",
                None, INCONSISTENCIA_ENTRADA, _VALIDACAO,
                "A nomenclatura informada (NCM ou NBS) não é aceita para a classificação tributária no tipo de "
                "documento fiscal (tpDoc) da operação."),
    "REG-031": ("Nomenclatura não permitida para o tipo de documento", "nomenclatura-nao-permitida-tipo-documento",
                None, INCONSISTENCIA_ENTRADA, _VALIDACAO,
                "O tipo de documento fiscal (tpDoc) da operação não admite o preenchimento de NCM ou NBS."),
    "REG-032": ("Imposto Seletivo não admitido pelo tipo de documento",
                "imposto-seletivo-nao-admitido-tipo-documento", "impostoSeletivo", INCONSISTENCIA_ENTRADA,
                _VALIDACAO, "O NCM/NBS informado é tributado pelo Imposto Seletivo, mas o tipo de documento "
                "fiscal (tpDoc) da operação não admite Imposto Seletivo."),
    "REG-033": ("Nomenclatura obrigatória não informada", "nomenclatura-obrigatoria-nao-informada", None,
                INCONSISTENCIA_ENTRADA, _VALIDACAO,
                "A classificação tributária possui anexo com NCM e NBS aplicáveis e exige o preenchimento de um "
                "deles, completo, para o tipo de documento fiscal (tpDoc) da operação."),
    "REG-034": ("Base de cálculo inconsistente", "base-calculo-inconsistente", "baseCalculo",
                INCONSISTENCIA_ENTRADA, _VALIDACAO,
                "A base de cálculo declarada no item diverge da base calculada a partir dos componentes "
                "informados"),
    "REG-035": ("Alíquota do Imposto Seletivo não informada", "aliquota-imposto-seletivo-nao-informada",
                "aliquotasNominais/impostoSeletivo", INCONSISTENCIA_ENTRADA, _VALIDACAO,
                "A NCM/NBS possui incidência do Imposto Seletivo, mas a alíquota ainda não foi definida em lei e "
                "não foi informada pelo usuário."),
    "REG-036": ("Alíquotas nominais informadas indevidamente", "aliquotas-nominais-informadas-indevidamente",
                "aliquotasNominais", INCONSISTENCIA_ENTRADA, _VALIDACAO,
                "As alíquotas nominais não podem ser informadas para fato gerador anterior a 01/01/2027, pois a "
                "calculadora resolve as alíquotas vigentes."),
    "REG-037": ("Alíquotas nominais não informadas", "aliquotas-nominais-nao-informadas", "aliquotasNominais",
                INCONSISTENCIA_ENTRADA, _VALIDACAO,
                "As alíquotas nominais (cbs, ibsEstadual e ibsMunicipal) devem ser informadas para fato gerador a "
                "partir de 01/01/2027."),
    "REG-038": ("Tipo de documento fiscal não tratado", "tipo-dfe-nao-tratado", "tpDoc", INCONSISTENCIA_ENTRADA,
                _VALIDACAO, "O tipo de documento fiscal (tpDoc) informado na operação não possui tratamento "
                "implementado na calculadora."),
    "CAL-001": ("Alíquota não encontrada", "aliquota-nao-encontrada", None, FALHA_TECNICA, _NAO_ENCONTRADA,
                "Não foi possível localizar a alíquota aplicável para os parâmetros tributários informados."),
    "CAL-002": ("Alíquota padrão não encontrada", "aliquota-padrao-nao-encontrada", None, FALHA_TECNICA,
                _NAO_ENCONTRADA, "A alíquota padrão (de referência) não foi encontrada na base para o tributo e "
                "período informados."),
    "CAL-003": ("Alíquota de referência não encontrada", "aliquota-referencia-nao-encontrada", None, FALHA_TECNICA,
                _NAO_ENCONTRADA, "A alíquota de referência necessária para o cálculo não está disponível na base "
                "de dados."),
    "CAL-004": ("Alíquota ad rem não encontrada", "aliquota-ad-rem-nao-encontrada", None, FALHA_TECNICA,
                _NAO_ENCONTRADA, "A alíquota ad rem (valor fixo por unidade) não foi localizada para o produto e "
                "período informados."),
    "CAL-005": ("Alíquota negativa", "aliquota-negativa", None, FALHA_TECNICA, _VALIDACAO,
                "O cálculo resultou em uma alíquota negativa, o que indica inconsistência nos parâmetros "
                "tributários."),
    "CAL-006": ("Tipo de alíquota desconhecido", "tipo-aliquota-desconhecido", None, FALHA_TECNICA, _VALIDACAO,
                "O tipo de alíquota retornado pela base de dados não é reconhecido pelo sistema."),
    "CAL-007": ("Classificação tributária em desenvolvimento", "erro-falta-implementacao", "cClassTrib",
                NAO_IMPLEMENTADO, _VALIDACAO,
                "A classificação tributária informada ainda não possui implementação completa no sistema."),
    "CAL-008": ("Erro no avaliador de expressão aritmética", "erro-avaliador-expressao", None, FALHA_TECNICA,
                _VALIDACAO, "Ocorreu um erro ao avaliar a expressão aritmética utilizada no cálculo do tributo."),
    "CAL-009": ("Tratamento tributário não encontrado", "tratamento-tributario-nao-encontrado", None,
                FALHA_TECNICA, _NAO_ENCONTRADA,
                "O tratamento tributário aplicável não foi localizado para a combinação de parâmetros "
                "informada."),
    "CAL-010": ("Percentual de redução não encontrado", "percentual-reducao-nao-encontrado", None, FALHA_TECNICA,
                _NAO_ENCONTRADA, "O percentual de redução de alíquota previsto para a classificação não foi "
                "encontrado na base."),
    "CAL-011": ("Fundamentação não encontrada", "fundamentacao-nao-encontrada", None, FALHA_TECNICA,
                _NAO_ENCONTRADA, "A fundamentação legal para a classificação tributária não foi localizada na "
                "base de dados."),
    "CAL-012": ("Tributo/situação tributária não encontrado", "tributo-situacao-tributaria-nao-encontrado", None,
                FALHA_TECNICA, _NAO_ENCONTRADA,
                "A combinação de tributo e situação tributária informada não foi localizada na base."),
    "CAL-999": ("Erro interno do sistema", "erro-interno-de-sistema", None, FALHA_TECNICA, _SISTEMA,
                "Erro interno inesperado durante o processamento do cálculo. Contate o suporte se o problema "
                "persistir."),
}

# CodigoValidacao: anotação -> (código, título, descrição)
_VALIDACOES = {
    "NotNull": ("VAL-001", "Campo obrigatório não informado",
                "O campo marcado como obrigatório não foi preenchido na requisição."),
    "NotBlank": ("VAL-002", "Campo obrigatório em branco", "O campo não pode estar em branco ou conter apenas espaços."),
    "NotEmpty": ("VAL-003", "Lista ou campo não pode estar vazio", "O campo ou lista informado não pode estar vazio."),
    "Size": ("VAL-004", "Tamanho fora do intervalo permitido",
             "O valor informado não atende aos limites de tamanho mínimo e/ou máximo definidos."),
    "Min": ("VAL-005", "Valor abaixo do mínimo permitido", "O valor informado é inferior ao mínimo aceito para o campo."),
    "Max": ("VAL-006", "Valor acima do máximo permitido", "O valor informado é superior ao máximo aceito para o campo."),
    "DecimalMin": ("VAL-007", "Valor decimal abaixo do mínimo permitido",
                   "O valor decimal informado é inferior ao mínimo aceito para o campo."),
    "DecimalMax": ("VAL-008", "Valor decimal acima do máximo permitido",
                   "O valor decimal informado é superior ao máximo aceito para o campo."),
    "Pattern": ("VAL-009", "Formato inválido",
                "O valor informado não atende ao padrão (formato) exigido para o campo."),
    "Digits": ("VAL-010", "Quantidade de dígitos inválida",
               "O valor informado não possui a quantidade de dígitos inteiros e/ou decimais esperada."),
    "Email": ("VAL-011", "E-mail inválido", "O endereço de e-mail informado não é válido."),
    "Positive": ("VAL-012", "Valor deve ser positivo", "O valor informado deve ser um número positivo (maior que zero)."),
    "PositiveOrZero": ("VAL-013", "Valor deve ser positivo ou zero",
                       "O valor informado deve ser um número positivo ou zero."),
    "AssertTrue": ("VAL-014", "Condição não atendida",
                   "A condição de validação (assert true) não foi atendida pelo valor informado."),
    "AssertFalse": ("VAL-015", "Condição atendida indevidamente",
                    "A condição de validação (assert false) foi atendida quando não deveria."),
    "_Desconhecido": ("VAL-000", "Erro de validação não categorizado",
                      "O tipo de validação aplicado não possui uma categoria específica."),
}
_VALIDACAO_POR_CODIGO = {codigo: (titulo, descricao) for codigo, titulo, descricao in _VALIDACOES.values()}


def codigo_da_anotacao(anotacao: str) -> str:
    """CodigoValidacao.codeFrom: o código VAL da anotação do Bean Validation (VAL-000 se não catalogada)."""
    return _VALIDACOES.get(anotacao, _VALIDACOES["_Desconhecido"])[0]


def descrever_erro(codigo) -> dict | None:
    """ErroService.buscarPorCodigo: {codigo, titulo, descricao}, ou None (404) fora do catálogo.

    Procura primeiro nos ProblemType com código, depois nos VAL. A busca é exata.
    """
    if codigo is None:
        return None
    if codigo in _PROBLEMAS:
        titulo, _, _, _, _, descricao = _PROBLEMAS[codigo]
        return {"codigo": codigo, "titulo": titulo, "descricao": descricao}
    if codigo in _VALIDACAO_POR_CODIGO:
        titulo, descricao = _VALIDACAO_POR_CODIGO[codigo]
        return {"codigo": codigo, "titulo": titulo, "descricao": descricao}
    return None


class ErroRR(Exception):
    """Uma exceção do Java com o que a resposta precisa dela.

    - `codigo`: REG-, CAL- ou VAL-; None para exceção sem código no ProblemType (erro de sistema).
    - `titulo`: o título do ProblemType (ou do CodigoValidacao).
    - `detalhe`: o getMessage() da exceção (ou, no VAL, o detalhe montado por buildValidationDetail).
    - `ponteiro`: o campo do ponteiro. Nos REG e CAL, o `campoDefault` do CodigoErro (relativo ao item;
      None aponta para o item). Nos VAL, o caminho do Bean Validation (`itens[0].cst`, `municipio`).
    - `status_http`: o status de `/regime-geral`, pela hierarquia Java.
    - `estado_item`: o estado que o item recebe no `/observabilidade`.
    - `caminho`: o fim do `type` de `/regime-geral` (`{base_url}/errors/{caminho}`).
    Nos VAL, `mensagem` é a mensagem da anotação e `valor` o valor recusado (texto como o Java o escreve,
    ou None).
    """

    def __init__(self, codigo, detalhe, *, ponteiro=None, titulo=None, status_http=None, caminho=None,
                 mensagem=None, valor=None):
        super().__init__(detalhe)
        self.codigo = codigo
        self.detalhe = detalhe
        self.mensagem = mensagem
        self.valor = valor
        if codigo in _PROBLEMAS:
            p_titulo, p_caminho, campo, estado, status, _ = _PROBLEMAS[codigo]
            self.titulo = p_titulo if titulo is None else titulo
            self.caminho = p_caminho if caminho is None else caminho
            self.ponteiro = campo if ponteiro is None else ponteiro
            self.estado_item = estado
            self.status_http = status if status_http is None else status_http
        elif codigo in _VALIDACAO_POR_CODIGO:
            self.titulo = _VALIDACAO_POR_CODIGO[codigo][0] if titulo is None else titulo
            self.caminho = CAMINHO_SISTEMA if caminho is None else caminho
            self.ponteiro = ponteiro
            self.estado_item = INCONSISTENCIA_ENTRADA
            self.status_http = 400 if status_http is None else status_http
        elif codigo is None:  # ProblemType sem CodigoErro: o item fica FALHA_TECNICA com CAL-999
            self.titulo = TITULO_SISTEMA if titulo is None else titulo
            self.caminho = CAMINHO_SISTEMA if caminho is None else caminho
            self.ponteiro = ponteiro
            self.estado_item = FALHA_TECNICA
            self.status_http = _SISTEMA if status_http is None else status_http
        else:
            raise ValueError(f"código de erro fora do catálogo: {codigo}")

    @classmethod
    def de_validacao(cls, anotacao, campo, mensagem, valor):
        """Uma violação do Bean Validation (buildValidationDetail do ObservabilidadeService)."""
        if valor is not None:
            detalhe = f"Campo '{campo}' {mensagem}, recebido: '{valor}'"
        else:
            detalhe = f"Campo '{campo}' {mensagem}"
        return cls(codigo_da_anotacao(anotacao), detalhe, ponteiro=campo, mensagem=mensagem, valor=valor)

    def no_caminho(self, prefixo):
        """A mesma violação com o caminho visto de fora (`cst` dentro de `itens[0]` vira `itens[0].cst`)."""
        campo = f"{prefixo}.{self.ponteiro}"
        anotacao = next(a for a, (c, _, _) in _VALIDACOES.items() if c == self.codigo)
        return ErroRR.de_validacao(anotacao, campo, self.mensagem, self.valor)


# --- respostas de erro --------------------------------------------------------------------------

def instante(agora=None) -> str:
    """Instant.now() como o Jackson escreve (ISO_INSTANT: fração em 0, 3, 6 ou 9 dígitos)."""
    if agora is None:
        agora = datetime.now(timezone.utc)
    if agora.tzinfo is not None:
        agora = agora.astimezone(timezone.utc)
    texto = agora.strftime("%Y-%m-%dT%H:%M:%S")
    micro = agora.microsecond
    if micro:
        texto += f".{micro:06d}" if micro % 1000 else f".{micro // 1000:03d}"
    return texto + "Z"


def corpo_problema(status, titulo, caminho, detalhe, base_url, instancia, agora=None) -> dict:
    """ApiExceptionHandler.createProblem: o ProblemDetail, na ordem em que a oficial o escreve."""
    return {
        "detail": detalhe,
        "instance": instancia,
        "status": status,
        "title": titulo,
        "type": f"{base_url}/errors/{caminho}",
        "timestamp": instante(agora),
    }


def corpo_de_erro(erro: ErroRR, base_url, instancia, agora=None) -> tuple:
    """(status, corpo) de `/regime-geral` para uma exceção lançada pelo serviço."""
    return erro.status_http, corpo_problema(erro.status_http, erro.titulo, erro.caminho, erro.detalhe, base_url,
                                            instancia, agora)


def corpo_ilegivel(detalhe, base_url, instancia, agora=None) -> tuple:
    """handleHttpMessageNotReadable: 400 com o título de erro de sistema e o texto do Spring."""
    return 400, corpo_problema(400, TITULO_SISTEMA, CAMINHO_SISTEMA, detalhe, base_url, instancia, agora)


def corpo_campos_invalidos(violacoes, base_url, instancia, agora=None) -> tuple:
    """handleMethodArgumentNotValid: 400 "Campos inválidos." com a lista `[campo: mensagem] `."""
    detalhe = "Um ou mais campos são inválidos - " + "".join(f"[{v.ponteiro}: {v.mensagem}] " for v in violacoes)
    corpo = corpo_problema(400, "Campos inválidos.", CAMINHO_SISTEMA, detalhe, base_url, instancia, agora)
    return 400, corpo


def corpo_erros_calculo(erros, instancia, agora=None) -> tuple:
    """ObservabilidadeExceptionHandler.handleErrosCalculoException: 422 com a lista `errors`."""
    return 422, {
        "detail": f"O cálculo contém {len(erros)} erro(s).",
        "instance": instancia,
        "status": 422,
        "title": "Erros no processamento do cálculo",
        "type": "about:blank",
        "timestamp": instante(agora),
        "errors": list(erros),
    }


def _detalhe(tipo, titulo, codigo, ponteiro, detalhe) -> dict:
    # ErroDetalhe (record): type, title, code, pointer, detail
    return {"type": tipo, "title": titulo, "code": codigo, "pointer": ponteiro, "detail": detalhe}


def detalhe_global_de_validacao(violacao: ErroRR, base_url) -> dict:
    """ObservabilidadeService.validarCamposGlobais: o `type` vai sem o /api, o ponteiro é o JSON Pointer."""
    ponteiro = "#/" + _indices_como_partes(violacao.ponteiro).replace(".", "/")
    return _detalhe(f"{base_url}{CAMINHO_ERROS}?codigo={violacao.codigo}", violacao.titulo, violacao.codigo,
                    ponteiro, violacao.detalhe)


def _indices_como_partes(campo):
    # ARRAY_INDEX.matcher(field).replaceAll("/$1"): `itens[0]` vira `itens/0`
    return re.sub(r"\[(\d+)]", r"/\1", campo)


def detalhe_de_item(erro: ErroRR, indice: int, base_url) -> dict:
    """ItemCollector.addErroValidacao e addErro: `type` com o /api, ponteiro `#/itens/{indice}[/campo]`.

    Exceção sem código (erro de sistema) vira CAL-999 com o título do ProblemType dela.
    """
    codigo = "CAL-999" if erro.codigo is None else erro.codigo
    campo = None if erro.codigo is None else erro.ponteiro
    ponteiro = f"#/itens/{indice}" if campo is None else f"#/itens/{indice}/{campo}"
    tipo = f"{base_url}/api{CAMINHO_ERROS}?codigo={codigo}"
    return _detalhe(tipo, erro.titulo, codigo, ponteiro, erro.detalhe)
