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

"""Entrada da skill cbs-ibs-regime-regular: o corpo como o Jackson o lê e o Bean Validation dele.

Port (calculadora-rtc-regime-regular, tag 1.7.2) de api/model/input: OperacaoInput, ItemOperacaoInput,
ImpostoSeletivoInput, AliquotasNominaisInput, AliquotasNominaisImpostoSeletivo, TributacaoRegularInput e
basecalculo/ComponentesBaseCalculoInput, e de config/serializer/OffsetDateTimeCustomDeserializer (com o
GenericCustomDeserializer).

`ler_operacao(corpo)` faz o que o Jackson 3 da oficial faz com o JSON já lido (números como int ou
Decimal): campo desconhecido é ignorado; texto aceita número e booleano (coerção de escalar); Integer e
Long aceitam texto aparado com um inteiro (vazio ou "null" é nulo) e número com fração (truncado);
BigDecimal aceita número e texto com número (vazio é nulo); objeto ou lista fora do lugar é JSON ilegível,
com a mensagem do Jackson. O primeiro defeito, na ordem do JSON, vence. A leitura tolerante é a mesma do
`simples_motor` da skill cbs-ibs-simples (`_ler_registro`, `_ler_inteiro`, `_ler_texto`), trocados os tipos.

`validar_bean(op)` devolve as violações do Bean Validation de `@Valid OperacaoInput` (o que o
/regime-geral recusa com 400 "Campos inválidos."), na ordem das anotações nos campos Java. O Hibernate
Validator devolve um conjunto sem ordem; com mais de uma violação a oficial pode listá-las em outra ordem.

O grupo gCompraGov fica guardado como veio, sem leitura nem validação: a skill o recusa antes do motor.
"""
from __future__ import annotations

import decimal
import re
import sys
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

_AQUI = Path(__file__).resolve().parent
if str(_AQUI) not in sys.path:
    sys.path.append(str(_AQUI))

import rr_erros  # noqa: E402  (precisa do caminho acima)

_PACOTE = "br.gov.serpro.rtc.api.model.input."
T_OPERACAO = _PACOTE + "OperacaoInput"
T_ITEM = _PACOTE + "ItemOperacaoInput"
T_IMPOSTO_SELETIVO = _PACOTE + "ImpostoSeletivoInput"
T_TRIBUTACAO_REGULAR = _PACOTE + "TributacaoRegularInput"
T_ALIQUOTAS = _PACOTE + "AliquotasNominaisInput"
T_ALIQUOTAS_IS = _PACOTE + "AliquotasNominaisImpostoSeletivo"
T_COMPONENTES = _PACOTE + "basecalculo.ComponentesBaseCalculoInput"
_T_LISTA_ITENS = "java.util.ArrayList<" + T_ITEM + ">"
_T_STRING = "java.lang.String"
_T_INTEGER = "java.lang.Integer"
_T_LONG = "java.lang.Long"
_T_BIG_DECIMAL = "java.math.BigDecimal"

_INT_MIN, _INT_MAX = -(2 ** 31), 2 ** 31 - 1
_LONG_MIN, _LONG_MAX = -(2 ** 63), 2 ** 63 - 1


class CorpoIlegivel(Exception):
    """HttpMessageNotReadableException: o corpo não cabe no contrato (HTTP 400). str() é o detalhe."""

    def __init__(self, detalhe):
        super().__init__(detalhe)
        self.detalhe = detalhe


class CorpoAusente(CorpoIlegivel):
    """Sem corpo (ou o literal null): "Required request body is missing", com a assinatura do controller."""

    def __init__(self):
        super().__init__(None)


# --- o modelo lido ------------------------------------------------------------------------------

@dataclass
class DataHora:
    """OffsetDateTime: o texto como veio e a data local dele (toLocalDate)."""
    texto: str
    data: date


@dataclass
class ImpostoSeletivo:
    cst: str | None = None
    c_class_trib: str | None = None
    base_calculo: Decimal | None = None
    quantidade: Decimal | None = None
    unidade: str | None = None
    imposto_informado: Decimal | None = None


@dataclass
class TributacaoRegular:
    cst: str | None = None
    c_class_trib: str | None = None


@dataclass
class AliquotasImpostoSeletivo:
    ad_valorem: Decimal | None = None
    ad_rem: Decimal | None = None


@dataclass
class AliquotasNominais:
    cbs: Decimal | None = None
    ibs_estadual: Decimal | None = None
    ibs_municipal: Decimal | None = None
    imposto_seletivo: AliquotasImpostoSeletivo | None = None


@dataclass
class ComponentesBaseCalculo:
    v_prod: Decimal | None = None
    v_frete: Decimal | None = None
    v_seg: Decimal | None = None
    v_outro: Decimal | None = None
    v_ii: Decimal | None = None
    v_is: Decimal | None = None
    v_desc: Decimal | None = None
    v_pis: Decimal | None = None
    v_cofins: Decimal | None = None
    v_icms: Decimal | None = None
    v_icms_uf_dest: Decimal | None = None
    v_fcp: Decimal | None = None
    v_fcp_uf_dest: Decimal | None = None
    v_icms_mono: Decimal | None = None
    v_issqn: Decimal | None = None

    def possui_componentes(self) -> bool:
        return any(getattr(self, campo) is not None for campo, _ in _CAMPOS_COMPONENTES)


@dataclass
class Item:
    """ItemOperacaoInput como chegou (o Java muda `ncm` em ajustarNcm; aqui também é mutável)."""
    numero: int | None = None
    ncm: str | None = None
    nbs: str | None = None
    cst: str | None = None
    c_class_trib: str | None = None
    base_calculo: Decimal | None = None
    quantidade: Decimal | None = None
    unidade: str | None = None
    imposto_seletivo: ImpostoSeletivo | None = None
    tributacao_regular: TributacaoRegular | None = None
    g_componentes_bc: ComponentesBaseCalculo | None = None
    aliquotas_nominais: AliquotasNominais | None = None


@dataclass
class Operacao:
    """OperacaoInput como chegou. `itens` é a lista lida (None onde o elemento veio nulo), ou None."""
    id: str | None = None
    versao: str | None = None
    dh_fato_gerador: DataHora | None = None
    data_hora_emissao: DataHora | None = None
    municipio: int | None = None
    uf: str | None = None
    tp_doc: int | None = None
    g_compra_gov: object = None
    itens: list | None = None

    def fato_gerador_aplicavel(self) -> date:
        """OperacaoInput.getFatoGeradorAplicavel: dhFatoGerador, senão dataHoraEmissao, senão REG-021."""
        if self.dh_fato_gerador is not None:
            return self.dh_fato_gerador.data
        if self.data_hora_emissao is not None:
            return self.data_hora_emissao.data
        raise rr_erros.ErroRR("REG-021", "A data do fato gerador não foi informada.")


# --- mensagens do Jackson -------------------------------------------------------------------------

def _natureza(valor):
    # (rótulo do valor para "from X value (token ...)", token)
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
    return f"JSON parse error: Cannot deserialize value of type `{tipo}` from {rotulo} value (token `JsonToken.{token}`)"


_ATE_ESPACO = "".join(chr(i) for i in range(0x21))  # String.trim tira tudo até U+0020


def _aparar(valor):
    return valor.strip(_ATE_ESPACO)


def _java_double(valor):
    """Double.toString do número (o Jackson lê número com fração como double para achar um construtor)."""
    for n in range(1, 18):  # o decimal mais curto que volta ao mesmo double
        candidato = decimal.Context(prec=n).plus(valor)
        if float(candidato) == float(valor):
            break
    if candidato.is_zero():
        return "-0.0" if candidato.is_signed() else "0.0"
    sinal = "-" if candidato < 0 else ""
    partes = abs(candidato).normalize().as_tuple()
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
    # registro recebendo escalar: o Jackson procura um construtor de um argumento
    if isinstance(valor, str):
        como = f"no String-argument constructor/factory method to deserialize from String value ('{_aparar(valor)}')"
    elif isinstance(valor, bool):
        como = ("no boolean/Boolean-argument constructor/factory method to deserialize from boolean value "
                f"({'true' if valor else 'false'})")
    elif isinstance(valor, int):
        if _INT_MIN <= valor <= _INT_MAX:
            como = f"no int/Int-argument constructor/factory method to deserialize from Number value ({valor})"
        elif _LONG_MIN <= valor <= _LONG_MAX:
            como = f"no long/Long-argument constructor/factory method to deserialize from Number value ({valor})"
        else:
            como = f"no BigInteger-argument constructor/factory method to deserialize from Number value ({valor})"
    else:
        como = ("no double/Double-argument constructor/factory method to deserialize from Number value "
                f"({_java_double(valor)})")
    return f"JSON parse error: Cannot construct instance of `{tipo}` (although at least one Creator exists): {como}"


def _texto_no_lugar_de_registro(tipo, texto):
    # BeanDeserializer.deserializeFromString: o texto é aparado; vazio é coerção recusada
    if _aparar(texto) == "":
        return (f'JSON parse error: Cannot coerce empty String ("") to `{tipo}` value (but could if coercion was '
                "enabled using `CoercionConfig`)")
    return _sem_construtor(tipo, texto)


# --- escalares -------------------------------------------------------------------------------------

def _ler_texto(valor, _nome=None):
    # StringDeserializer: escalar vira texto
    if valor is None or isinstance(valor, str):
        return valor
    if isinstance(valor, bool):
        return "true" if valor else "false"
    if isinstance(valor, (int, Decimal)):
        return str(valor)
    raise CorpoIlegivel(_de_valor(_T_STRING, valor))


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


def _ler_numero_inteiro(valor, tipo, minimo, maximo, rotulo_faixa):
    if valor is None:
        return None
    if isinstance(valor, (bool, dict, list)):
        raise CorpoIlegivel(_de_valor(tipo, valor))
    if isinstance(valor, (int, Decimal)):
        if valor < minimo or valor > maximo:
            raise CorpoIlegivel(f"JSON parse error: Numeric value ({valor}) out of range of {rotulo_faixa}")
        return int(valor)  # número com fração: truncado (ACCEPT_FLOAT_AS_INT)
    texto = _aparar(valor)
    if texto in ("", "null"):
        return None
    numero = _parse_inteiro_java(texto, minimo, maximo)
    if numero is None:
        raise CorpoIlegivel(f'JSON parse error: Cannot deserialize value of type `{tipo}` from String "{texto}": '
                            f"not a valid `{tipo}` value")
    return numero


def _ler_inteiro(valor, _nome=None):
    # NumberDeserializers.IntegerDeserializer
    return _ler_numero_inteiro(valor, _T_INTEGER, _INT_MIN, _INT_MAX, "`int` (-2147483648 - 2147483647)")


def _ler_longo(valor, _nome=None):
    # NumberDeserializers.LongDeserializer
    return _ler_numero_inteiro(valor, _T_LONG, _LONG_MIN, _LONG_MAX,
                               "`long` (-9223372036854775808 - 9223372036854775807)")


_NUMERO_JAVA = re.compile(r"([+-]?)(\d*)(?:\.(\d*))?(?:[eE]([+-]?\d+))?")


def _sem_sinal_de_zero(numero: Decimal) -> Decimal:
    # BigDecimal não tem zero negativo
    return numero.copy_abs() if numero.is_zero() else numero


def _ler_decimal(valor, _nome=None):
    # NumberDeserializers.BigDecimalDeserializer
    if valor is None:
        return None
    if isinstance(valor, (bool, dict, list)):
        raise CorpoIlegivel(_de_valor(_T_BIG_DECIMAL, valor))
    if isinstance(valor, int):
        return Decimal(valor)
    if isinstance(valor, Decimal):
        return _sem_sinal_de_zero(valor)
    texto = _aparar(valor)
    if texto in ("", "null"):
        return None
    achou = _NUMERO_JAVA.fullmatch(texto)  # new BigDecimal(String): dígitos pelo Character.isDigit
    if achou is None or not (achou.group(2) or achou.group(3)):
        raise CorpoIlegivel(f'JSON parse error: Cannot deserialize value of type `{_T_BIG_DECIMAL}` from String '
                            f'"{texto}": not a valid representation')
    numero = "".join(c if c in "+-.eE" else str(unicodedata.decimal(c)) for c in texto)
    return _sem_sinal_de_zero(Decimal(numero))


_DATA_HORA = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}([+-][0-9]{2}:[0-9]{2}|Z)")
_FORMATO_DATA_HORA = "yyyy-MM-dd'T'HH:mm:ssXXX"
_EXEMPLO_DATA_HORA = "2026-01-05T13:45:00-03:00 ou 2026-05-22T10:57:23Z"


def _dias_do_mes(ano, mes):
    if mes == 2:
        return 29 if ano % 4 == 0 and (ano % 100 != 0 or ano % 400 == 0) else 28
    return 30 if mes in (4, 6, 9, 11) else 31


def _ler_data_hora(valor, nome):
    """OffsetDateTimeCustomDeserializer: o padrão do GenericCustomDeserializer e o ISO_OFFSET_DATE_TIME."""
    if valor is None:
        return None
    if isinstance(valor, dict):
        texto = "{"
    elif isinstance(valor, list):
        texto = "["
    else:
        texto = _ler_texto(valor)
    data = None
    if _DATA_HORA.fullmatch(texto):
        ano, mes, dia = int(texto[0:4]), int(texto[5:7]), int(texto[8:10])
        hora, minuto, segundo = int(texto[11:13]), int(texto[14:16]), int(texto[17:19])
        fuso = texto[19:]
        fuso_ok = fuso == "Z" or (int(fuso[1:3]) <= 18 and int(fuso[4:6]) <= 59
                                  and int(fuso[1:3]) * 60 + int(fuso[4:6]) <= 18 * 60)
        if (1 <= mes <= 12 and 1 <= dia <= _dias_do_mes(ano, mes) and hora <= 23 and minuto <= 59 and segundo <= 59
                and fuso_ok and ano >= 1):  # o ano 0000 vale no Java; aqui fica de fora (date não o representa)
            data = date(ano, mes, dia)
    if data is None:
        raise CorpoIlegivel(f"JSON parse error: Campo '{nome}' [{texto}] inválido. Formato esperado: "
                            f"'{_FORMATO_DATA_HORA}'. Exemplo de valor: '{_EXEMPLO_DATA_HORA}'")
    return DataHora(texto, data)


def _bruto(valor, _nome=None):
    return valor


# --- registros ----------------------------------------------------------------------------------------

def _registro(tipo, classe, campos):
    def ler(no, _nome=None):
        if no is None:
            return None
        if not isinstance(no, dict):
            if isinstance(no, list):
                raise CorpoIlegivel(_de_valor(tipo, no))
            if isinstance(no, str):
                raise CorpoIlegivel(_texto_no_lugar_de_registro(tipo, no))
            raise CorpoIlegivel(_sem_construtor(tipo, no))
        lido = {}
        por_nome = {nome: (atributo, leitor) for nome, atributo, leitor in campos}
        for nome, valor in no.items():  # na ordem do JSON: o primeiro defeito vence, o último valor fica
            if nome not in por_nome:
                continue
            atributo, leitor = por_nome[nome]
            lido[atributo] = leitor(valor, nome)
        return classe(**lido)
    return ler


_CAMPOS_COMPONENTES = (
    ("v_prod", "vProd"), ("v_frete", "vFrete"), ("v_seg", "vSeg"), ("v_outro", "vOutro"), ("v_ii", "vII"),
    ("v_is", "vIS"), ("v_desc", "vDesc"), ("v_pis", "vPIS"), ("v_cofins", "vCOFINS"), ("v_icms", "vICMS"),
    ("v_icms_uf_dest", "vICMSUFDest"), ("v_fcp", "vFCP"), ("v_fcp_uf_dest", "vFCPUFDest"),
    ("v_icms_mono", "vICMSMono"), ("v_issqn", "vISSQN"),
)

_ler_imposto_seletivo = _registro(T_IMPOSTO_SELETIVO, ImpostoSeletivo, (
    ("cst", "cst", _ler_texto), ("cClassTrib", "c_class_trib", _ler_texto),
    ("baseCalculo", "base_calculo", _ler_decimal), ("quantidade", "quantidade", _ler_decimal),
    ("unidade", "unidade", _ler_texto), ("impostoInformado", "imposto_informado", _ler_decimal)))
_ler_tributacao_regular = _registro(T_TRIBUTACAO_REGULAR, TributacaoRegular, (
    ("cst", "cst", _ler_texto), ("cClassTrib", "c_class_trib", _ler_texto)))
_ler_aliquotas_is = _registro(T_ALIQUOTAS_IS, AliquotasImpostoSeletivo, (
    ("adValorem", "ad_valorem", _ler_decimal), ("adRem", "ad_rem", _ler_decimal)))
_ler_aliquotas = _registro(T_ALIQUOTAS, AliquotasNominais, (
    ("cbs", "cbs", _ler_decimal), ("ibsEstadual", "ibs_estadual", _ler_decimal),
    ("ibsMunicipal", "ibs_municipal", _ler_decimal), ("impostoSeletivo", "imposto_seletivo", _ler_aliquotas_is)))
_ler_componentes = _registro(T_COMPONENTES, ComponentesBaseCalculo,
                             tuple((nome, atributo, _ler_decimal) for atributo, nome in _CAMPOS_COMPONENTES))
_ler_item = _registro(T_ITEM, Item, (
    ("numero", "numero", _ler_inteiro), ("ncm", "ncm", _ler_texto), ("nbs", "nbs", _ler_texto),
    ("cst", "cst", _ler_texto), ("cClassTrib", "c_class_trib", _ler_texto),
    ("baseCalculo", "base_calculo", _ler_decimal), ("quantidade", "quantidade", _ler_decimal),
    ("unidade", "unidade", _ler_texto), ("impostoSeletivo", "imposto_seletivo", _ler_imposto_seletivo),
    ("tributacaoRegular", "tributacao_regular", _ler_tributacao_regular),
    ("gComponentesBC", "g_componentes_bc", _ler_componentes),
    ("aliquotasNominais", "aliquotas_nominais", _ler_aliquotas)))


def _ler_itens(lista, _nome=None):
    if lista is None:
        return None
    if not isinstance(lista, list):
        raise CorpoIlegivel(_de_valor(_T_LISTA_ITENS, lista))
    return [_ler_item(elemento) for elemento in lista]


_ler_operacao = _registro(T_OPERACAO, Operacao, (
    ("id", "id", _ler_texto), ("versao", "versao", _ler_texto),
    ("dhFatoGerador", "dh_fato_gerador", _ler_data_hora), ("dataHoraEmissao", "data_hora_emissao", _ler_data_hora),
    ("municipio", "municipio", _ler_longo), ("uf", "uf", _ler_texto), ("tpDoc", "tp_doc", _ler_inteiro),
    ("gCompraGov", "g_compra_gov", _bruto), ("itens", "itens", _ler_itens)))


def ler_operacao(corpo: object) -> Operacao:
    """O corpo JSON (já lido, números como int ou Decimal) como o Jackson monta o OperacaoInput.

    Levanta CorpoAusente sem corpo (None) e CorpoIlegivel quando o corpo não cabe no contrato.
    """
    if corpo is None:
        raise CorpoAusente()
    return _ler_operacao(corpo)


# --- Bean Validation ------------------------------------------------------------------------------------

def _texto_java(valor) -> str:
    # String.format("%s", valor) do valor recusado
    if isinstance(valor, list):
        return "[" + ", ".join(_texto_java(v) for v in valor) + "]"
    return str(valor)


def _tamanho_java(texto: str) -> int:
    # String.length(): unidades UTF-16
    return len(texto.encode("utf-16-le")) // 2


def _violacoes(campo, valor, regras):
    """As violações das anotações de um campo, na ordem em que estão no Java."""
    saida = []
    for regra in regras:
        nome = regra[0]
        mensagem = None
        if nome == "NotNull":
            if valor is None:
                mensagem = "must not be null"
        elif nome == "NotEmpty":
            if valor is None or len(valor) == 0:
                mensagem = "must not be empty"
        elif valor is None:
            continue
        elif nome == "Min":
            if valor < regra[1]:
                mensagem = f"must be greater than or equal to {regra[1]}"
        elif nome == "Max":
            if valor > regra[1]:
                mensagem = f"must be less than or equal to {regra[1]}"
        elif nome == "Size":
            if not regra[1] <= _tamanho_java(valor) <= regra[2]:
                mensagem = f"size must be between {regra[1]} and {regra[2]}"
        elif nome == "Pattern":  # \d+ do java.util.regex: só dígitos ASCII, casando o texto inteiro
            if not re.fullmatch(r"[0-9]+", valor):
                mensagem = "Informar somente dígitos"
        elif nome == "PositiveOrZero":
            if valor < 0:
                mensagem = "must be greater than or equal to 0"
        elif nome == "Digits":  # DigitsValidatorForNumber: BigDecimal sem stripTrailingZeros
            _, digitos, expoente = valor.as_tuple()
            escala = -expoente
            inteiros = len(digitos) - escala
            fracao = max(escala, 0)
            if inteiros > regra[1] or fracao > regra[2]:
                mensagem = f"numeric value out of bounds (<{regra[1]} digits>.<{regra[2]} digits> expected)"
        elif nome == "DecimalMax":
            if valor > Decimal(regra[1]):
                mensagem = f"must be less than or equal to {regra[1]}"
        elif nome == "DecimalMin":
            if valor < Decimal(regra[1]):
                mensagem = f"must be greater than or equal to {regra[1]}"
        else:
            raise ValueError(f"anotação sem port: {nome}")
        if mensagem is not None:
            recusado = None if valor is None else _texto_java(valor)
            saida.append(rr_erros.ErroRR.de_validacao(nome, campo, mensagem, recusado))
    return saida


class _Valid(tuple):
    """@Valid: o registro de dentro é validado com as regras dele, com o caminho `campo.`."""


_CODIGO = (("NotNull",), ("Pattern",), ("Size", 3, 3))
_CLASSIFICACAO = (("NotNull",), ("Pattern",), ("Size", 6, 6))
_ALIQUOTA = (("PositiveOrZero",), ("Digits", 3, 4), ("DecimalMax", "100.00"))

# (atributo, nome no JSON, anotações ou o validador do registro com @Valid)
_REGRAS_IMPOSTO_SELETIVO = (
    ("cst", "cst", _CODIGO), ("c_class_trib", "cClassTrib", _CLASSIFICACAO),
    ("base_calculo", "baseCalculo", (("NotNull",), ("PositiveOrZero",), ("Digits", 13, 2))),
    ("quantidade", "quantidade", (("PositiveOrZero",), ("Digits", 11, 4))),
    ("imposto_informado", "impostoInformado", (("PositiveOrZero",), ("Digits", 13, 2))),
)
_REGRAS_TRIBUTACAO_REGULAR = (("cst", "cst", _CODIGO), ("c_class_trib", "cClassTrib", _CLASSIFICACAO))
_REGRAS_ALIQUOTAS_IS = (("ad_valorem", "adValorem", _ALIQUOTA), ("ad_rem", "adRem", (("DecimalMin", "0.00"),)))
_REGRAS_ALIQUOTAS = (
    ("cbs", "cbs", _ALIQUOTA), ("ibs_estadual", "ibsEstadual", _ALIQUOTA),
    ("ibs_municipal", "ibsMunicipal", _ALIQUOTA), ("imposto_seletivo", "impostoSeletivo", _Valid(_REGRAS_ALIQUOTAS_IS)),
)
_REGRAS_COMPONENTES = tuple((atributo, nome, (("PositiveOrZero",),)) for atributo, nome in _CAMPOS_COMPONENTES)
_REGRAS_ITEM = (
    ("numero", "numero", (("NotNull",), ("Min", 1), ("Max", 9999999))),
    ("cst", "cst", _CODIGO), ("c_class_trib", "cClassTrib", _CLASSIFICACAO),
    ("base_calculo", "baseCalculo", (("PositiveOrZero",), ("Digits", 13, 2))),
    ("quantidade", "quantidade", (("Digits", 11, 4),)),
    ("imposto_seletivo", "impostoSeletivo", _Valid(_REGRAS_IMPOSTO_SELETIVO)),
    ("tributacao_regular", "tributacaoRegular", _Valid(_REGRAS_TRIBUTACAO_REGULAR)),
    ("g_componentes_bc", "gComponentesBC", _Valid(_REGRAS_COMPONENTES)),
    ("aliquotas_nominais", "aliquotasNominais", _Valid(_REGRAS_ALIQUOTAS)),
)
_REGRAS_OPERACAO = (
    ("id", "id", (("NotNull",),)), ("versao", "versao", (("NotNull",),)),
    ("municipio", "municipio", (("NotNull",), ("Min", 0), ("Max", 9999999))),
    ("uf", "uf", (("Size", 2, 2),)), ("tp_doc", "tpDoc", (("Min", 1), ("Max", 999))),
    ("itens", "itens", (("NotEmpty",),)),
)


def _validar_registro(objeto, regras, prefixo=""):
    saida = []
    for atributo, nome, regra in regras:
        valor = getattr(objeto, atributo)
        campo = f"{prefixo}{nome}"
        if isinstance(regra, _Valid):
            if valor is not None:
                saida.extend(_validar_registro(valor, regra, campo + "."))
        else:
            saida.extend(_violacoes(campo, valor, regra))
    return saida


def validar_item(item: Item) -> list:
    """Validator.validate(item): as violações com o caminho a partir do item (`cst`, `impostoSeletivo.cst`)."""
    return _validar_registro(item, _REGRAS_ITEM)


def validar_global(op: Operacao) -> list:
    """As violações da operação fora dos itens (as que o /observabilidade devolve como erro global)."""
    return _validar_registro(op, _REGRAS_OPERACAO)


def validar_bean(op: Operacao) -> list:
    """@Valid OperacaoInput: as violações da operação e de cada item (`itens[0].cst`), sem ordem garantida no
    Java; aqui, a dos campos e anotações, com os itens (cascata do campo `itens`) depois dos campos dela."""
    saida = validar_global(op)
    for indice, item in enumerate(op.itens or []):
        if item is not None:
            saida.extend(v.no_caminho(f"itens[{indice}]") for v in validar_item(item))
    return saida
