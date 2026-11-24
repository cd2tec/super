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
"""Avaliador das expressões do tratamento tributário (coluna TRTR_EXPRESSAO_* da base da Receita).

Espelha AvaliadorExpressaoAritmetica.java (calculadora-rtc 1.7.2, JEXL com aritmética BigDecimal):
  - soma, subtração e multiplicação são exatas (sem arredondar);
  - só a divisão usa o contexto de 34 dígitos (numeros.dividir), e divisão por zero é erro;
  - literal inteiro vira BigDecimal de escala 0; literal com ponto é double no JEXL e entra como
    BigDecimal(Double.toString), então "1.00" vira 1.0 (escala 1);
  - expressão que é só o nome de uma variável devolve o valor sem conta;
  - o resultado não é arredondado aqui.

Gramática (descendente recursivo, sem eval):
  expr  := termo (('+' | '-') termo)*
  termo := unario (('*' | '/') unario)*
  unario := '-' unario | primario
  primario := NUMERO | VARIAVEL | '(' expr ')'
Qualquer outro token levanta ValueError.
"""
import decimal
import re
from decimal import Context, Decimal

import numeros

# Os 21 nomes de VariavelExpressao.java.
VARIAVEIS = (
    "baseCalculo", "impostoSeletivoCalculado", "baseCalculoInformada", "impostoSeletivoInformado",
    "aliquotaEfetiva", "redutor", "ajuste", "aliquotaAdRem", "aliquotaAdValorem", "aliquotaReferencia",
    "aliquotaPadraoOuReferencia", "percentualReducao", "pRedutorCompraGov", "aliquota", "quantidade",
    "tributoCalculado", "percentualDiferimento", "aliquotaAdRemPrincipal", "aliquotaAdRemSecundaria",
    "variacaoPontoPercentual", "pBio",
)

# Soma exata: precisão máxima e erro se algum dígito se perdesse.
_EXATO = Context(prec=decimal.MAX_PREC, Emin=decimal.MIN_EMIN, Emax=decimal.MAX_EMAX,
                 traps=[decimal.InvalidOperation, decimal.DivisionByZero, decimal.Overflow, decimal.Inexact])

_TOKEN = re.compile(r"\s*(?:(\d+\.\d+|\d+)|([A-Za-z_][A-Za-z0-9_]*)|(.))", re.DOTALL)


def _sem_sinal_de_zero(v):
    # BigDecimal não tem zero negativo.
    return abs(v) if v.is_zero() else v


def _tokenizar(texto):
    tokens = []
    pos = 0
    while pos < len(texto):
        m = _TOKEN.match(texto, pos)
        if m is None:
            break
        pos = m.end()
        num, nome, outro = m.groups()
        if num is not None:
            tokens.append(("num", num))
        elif nome is not None:
            tokens.append(("var", nome))
        else:
            tokens.append(("op", outro))
    return tokens


def _literal(texto):
    if "." in texto:
        # Double do JEXL convertido por toString.
        return Decimal(repr(float(texto)))
    return Decimal(texto)


class _Parser:
    def __init__(self, texto, variaveis):
        self.tokens = _tokenizar(texto)
        self.i = 0
        self.variaveis = variaveis

    def _ver(self):
        return self.tokens[self.i] if self.i < len(self.tokens) else (None, None)

    def _tomar(self):
        t = self._ver()
        self.i += 1
        return t

    def analisar(self):
        v = self._expr()
        if self.i != len(self.tokens):
            raise ValueError(f"token inesperado na expressão: {self._ver()[1]!r}")
        return v

    def _expr(self):
        v = self._termo()
        while self._ver() in (("op", "+"), ("op", "-")):
            op = self._tomar()[1]
            d = self._termo()
            v = _sem_sinal_de_zero(_EXATO.add(v, d) if op == "+" else _EXATO.subtract(v, d))
        return v

    def _termo(self):
        v = self._unario()
        while self._ver() in (("op", "*"), ("op", "/")):
            op = self._tomar()[1]
            d = self._unario()
            v = _sem_sinal_de_zero(_EXATO.multiply(v, d) if op == "*" else numeros.dividir(v, d))
        return v

    def _unario(self):
        if self._ver() == ("op", "-"):
            self._tomar()
            return _sem_sinal_de_zero(self._unario().copy_negate())
        return self._primario()

    def _primario(self):
        tipo, valor = self._tomar()
        if tipo == "num":
            return _literal(valor)
        if tipo == "var":
            if valor not in VARIAVEIS:
                raise ValueError(f"variável desconhecida: {valor!r}")
            v = self.variaveis.get(valor)
            if v is None:
                raise ValueError(f"variável sem valor: {valor!r}")
            return v
        if (tipo, valor) == ("op", "("):
            v = self._expr()
            if self._tomar() != ("op", ")"):
                raise ValueError("parêntese não fechado")
            return v
        raise ValueError(f"token inesperado na expressão: {valor!r}")


def avaliar(expressao, variaveis):
    """Avalia a expressão com as variáveis dadas (dict nome -> Decimal) e devolve um Decimal."""
    texto = expressao.strip()
    if texto in variaveis and variaveis[texto] is not None:
        return variaveis[texto]
    return _Parser(texto, variaveis).analisar()
