"""Política numérica da skill cbs-ibs-simples.

Port de ArredondamentoUtils.java (calculadora-rtc-simples-nacional, tag 1.0.2), na ordem
do arquivo Java. Regras:

- Soma, subtração, multiplicação e as conversões entre percentual e fração são exatas.
  Nenhum arredondamento interno e nenhuma escala fixa.
- A divisão é a única operação que pode não ter resultado finito: usa CONTEXTO_DIVISAO,
  com 34 dígitos significativos (não casas decimais).
- Cada número publicado é arredondado uma vez, no cálculo: valor fiscal com 2 casas,
  alíquota publicada com 4, alíquota de documento com as casas do documento.
- Somas usam os valores publicados. Quando um valor é repartido, o rateio fecha o total.
- Modo de arredondamento: ROUND_HALF_EVEN (ABNT NBR 5891), convenção da calculadora oficial.

Todas as contas rodam em decimal.Context locais, nunca no contexto global. Toda função
recebe e devolve Decimal ou None, e None em qualquer operando devolve None. Só biblioteca
padrão.
"""
import decimal
from decimal import (
    ROUND_DOWN,
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    DivisionByZero,
    Inexact,
    InvalidOperation,
    Overflow,
)

# --- Parâmetros ---------------------------------------------------------------------

MODO = ROUND_HALF_EVEN
ESCALA_VALOR = 2
ESCALA_ALIQUOTA_MIN = 2
ESCALA_ALIQUOTA_MAX = 4
ESCALA_QUANTIDADE = 4

# Divisão: 34 dígitos significativos (decimal128), HALF_EVEN.
CONTEXTO_DIVISAO = Context(
    prec=34,
    rounding=MODO,
    Emin=decimal.MIN_EMIN,
    Emax=decimal.MAX_EMAX,
    capitals=1,
    clamp=0,
    flags=[],
    traps=[InvalidOperation, DivisionByZero, Overflow],
)

# Aritmética exata: precisão máxima e armadilha em Inexact, para que qualquer perda
# de dígito vire erro em vez de arredondamento silencioso.
_EXATO = Context(
    prec=decimal.MAX_PREC,
    rounding=MODO,
    Emin=decimal.MIN_EMIN,
    Emax=decimal.MAX_EMAX,
    capitals=1,
    clamp=0,
    flags=[],
    traps=[InvalidOperation, DivisionByZero, Overflow, Inexact],
)

# Arredondamento de apresentação e de valor publicado: mesma precisão, sem a armadilha
# de Inexact (arredondar é exatamente perder dígitos).
_ARREDONDA = Context(
    prec=decimal.MAX_PREC,
    rounding=MODO,
    Emin=decimal.MIN_EMIN,
    Emax=decimal.MAX_EMAX,
    capitals=1,
    clamp=0,
    flags=[],
    traps=[InvalidOperation, DivisionByZero, Overflow],
)

_UM = Decimal(1)


def _casas(n):
    """Decimal com expoente -n, o molde do quantize (ex.: 2 devolve 0.01)."""
    return Decimal((0, (1,), -n))


def _sem_sinal_de_zero(valor):
    """O BigDecimal do Java não tem zero negativo. Aqui o zero sai sempre positivo."""
    return abs(valor) if valor.is_zero() else valor


def _arredondar(valor, casas, modo=MODO):
    return _sem_sinal_de_zero(valor.quantize(_casas(casas), rounding=modo, context=_ARREDONDA))


# --- Aritmética interna (exata) -----------------------------------------------------

def multiplicar(v1, v2):
    """Multiplicação exata (sem arredondamento); None se algum operando for None."""
    if v1 is None or v2 is None:
        return None
    return _EXATO.multiply(v1, v2)


def subtrair(v1, v2):
    """Subtração exata (sem arredondamento); None se algum operando for None."""
    if v1 is None or v2 is None:
        return None
    return _EXATO.subtract(v1, v2)


def dividir(dividendo, divisor):
    """Divisão com CONTEXTO_DIVISAO (34 dígitos significativos, HALF_EVEN).

    None se algum operando for None. Use só quando o quociente puder não ser finito.
    """
    if dividendo is None or divisor is None:
        return None
    return CONTEXTO_DIVISAO.divide(dividendo, divisor)


def totalizar(valores):
    """Soma exata de um iterável de valores (sem arredondamento). Vazio devolve 0."""
    total = Decimal(0)
    for valor in valores:
        total = _EXATO.add(total, valor)
    return total


def percentual_para_fracao(percentual):
    """Pontos percentuais para fração (1.25 vira 0.0125), exata."""
    return None if percentual is None else _EXATO.scaleb(percentual, -2)


def fracao_para_percentual(fracao):
    """Fração para pontos percentuais (0.006132 vira 0.6132), exata."""
    return None if fracao is None else _EXATO.scaleb(fracao, 2)


# --- Números publicados (um arredondamento por número, no cálculo) --------------------

def arredondar_valor(valor):
    """Valor fiscal: o valor exato arredondado UMA vez a ESCALA_VALOR casas, HALF_EVEN."""
    return None if valor is None else _arredondar(valor, ESCALA_VALOR)


def arredondar_aliquota_publicada(aliquota):
    """Alíquota publicada (pontos percentuais): arredondada UMA vez a 4 casas, HALF_EVEN."""
    return None if aliquota is None else _arredondar(aliquota, ESCALA_ALIQUOTA_MAX)


def arredondar_aliquota(aliquota, casas):
    """Alíquota do leiaute de um documento, arredondada a `casas` casas, HALF_EVEN.

    `casas` None mantém a precisão integral (devolve a alíquota sem mudança).
    """
    if aliquota is None or casas is None:
        return aliquota
    return _arredondar(aliquota, casas)


def ratear_parcelas(parcelas_exatas):
    """Rateio de um valor fiscal em parcelas que somam o total.

    O total é a soma exata das parcelas, arredondada UMA vez. Cada parcela recebe o seu
    valor exato truncado no centavo. Os centavos que faltam para o total vão, um a um, às
    parcelas de maior resto (empate: a de menor posição). O resultado tem a ordem da entrada
    e a soma das parcelas devolvidas é igual ao total.
    """
    parcelas_exatas = list(parcelas_exatas)
    total = arredondar_valor(totalizar(parcelas_exatas))
    parcelas = []
    restos = []
    distribuido = Decimal(0)
    for exata in parcelas_exatas:
        truncada = _arredondar(exata, ESCALA_VALOR, ROUND_DOWN)
        parcelas.append(truncada)
        restos.append(_EXATO.subtract(exata, truncada))
        distribuido = _EXATO.add(distribuido, truncada)
    # maior resto primeiro; a ordenação é estável também com reverse, então o empate
    # fica com a menor posição
    ordem = sorted(range(len(parcelas)), key=restos.__getitem__, reverse=True)
    centavo = _casas(ESCALA_VALOR)
    faltantes = _EXATO.scaleb(_EXATO.subtract(total, distribuido), ESCALA_VALOR)
    if faltantes != faltantes.to_integral_value():
        raise ArithmeticError("rateio sem centavos inteiros")
    for k in range(int(faltantes)):
        posicao = ordem[k % len(ordem)]
        parcelas[posicao] = _EXATO.add(parcelas[posicao], centavo)
    return parcelas


def ratear_valor(total, pesos):
    """Reparte `total` entre as parcelas proporcionais a `pesos`, fechando o total.

    Cada parcela exata é total x peso / soma dos pesos (divisão de 34 dígitos). O arredondamento
    e a distribuição dos centavos seguem ratear_parcelas. None em `total` ou `pesos` devolve None.
    """
    if total is None or pesos is None:
        return None
    pesos = list(pesos)
    soma = totalizar(pesos)
    exatas = [dividir(multiplicar(total, peso), soma) for peso in pesos]
    return ratear_parcelas(exatas)


# --- Formatação para apresentação -----------------------------------------------------

def formatar_moeda(v):
    """Valor monetário com exatamente 2 casas decimais (ex.: "804.37")."""
    return format(_arredondar(v, ESCALA_VALOR), "f")


def formatar_aliquota(v):
    """Alíquota com 2 a 4 casas: arredonda a 4, tira zeros à direita e garante o mínimo de 2.

    Exemplos: 0.2080 vira "0.208", 0.2000 vira "0.20", 60.0000 vira "60.00".
    """
    sem_zeros = _ARREDONDA.normalize(_arredondar(v, ESCALA_ALIQUOTA_MAX))
    if -sem_zeros.as_tuple().exponent < ESCALA_ALIQUOTA_MIN:
        sem_zeros = _arredondar(sem_zeros, ESCALA_ALIQUOTA_MIN)
    return format(_sem_sinal_de_zero(sem_zeros), "f")


def formatar_quantidade(v):
    """Quantidade: inteiro quando possível, senão 4 casas decimais HALF_EVEN."""
    if _ARREDONDA.normalize(v).as_tuple().exponent >= 0:
        return str(int(v))
    return format(_arredondar(v, ESCALA_QUANTIDADE), "f")
