"""Testes da política numérica da skill cbs-ibs-simples.

Portados método a método de Teste_ArredondamentoUtils.java (calculadora-rtc-simples-nacional,
tag 1.0.2), com os mesmos valores. Os testes com sorteio usam random.Random com a mesma
semente e as mesmas faixas do Java (a sequência sorteada é outra, a propriedade é a mesma).
"""
import importlib.util
import random
import sys
import unittest
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "core" / "cd2-super" / "skills" / "cbs-ibs-simples"


def load_module(name, path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


numeros = load_module("cbs_ibs_simples_numeros", SKILL / "scripts" / "numeros.py")

D = Decimal
CBS_INTEGRAL = D("1.6839379378389463610262954")


def textos(valores):
    """Texto de cada valor: confere o valor e a escala, como o equals do BigDecimal."""
    return [str(v) for v in valores]


class ArredondamentoTest(unittest.TestCase):
    def test_modo_unico_e_half_even(self):
        self.assertEqual(numeros.MODO, ROUND_HALF_EVEN)
        self.assertEqual(numeros.CONTEXTO_DIVISAO.rounding, ROUND_HALF_EVEN)
        self.assertEqual(numeros.CONTEXTO_DIVISAO.prec, 34)
        self.assertEqual(numeros.ESCALA_VALOR, 2)
        self.assertEqual(numeros.ESCALA_ALIQUOTA_MIN, 2)
        self.assertEqual(numeros.ESCALA_ALIQUOTA_MAX, 4)

    def test_multiplicar_e_subtrair_sao_exatos_sem_escala_fixa(self):
        # 0,9 x alíquota integral: nenhuma casa perdida.
        self.assertEqual(str(numeros.multiplicar(D("0.9"), CBS_INTEGRAL)),
                         "1.51554414405505172492366586")
        self.assertEqual(str(numeros.subtrair(D(1), D("0.10"))), "0.90")
        self.assertIsNone(numeros.multiplicar(None, D(1)))
        self.assertIsNone(numeros.subtrair(D(1), None))

    def test_conversao_percentual_fracao_e_exata_nos_dois_sentidos(self):
        fracao = numeros.percentual_para_fracao(CBS_INTEGRAL)
        self.assertEqual(str(fracao), "0.016839379378389463610262954")
        self.assertEqual(str(numeros.fracao_para_percentual(fracao)), str(CBS_INTEGRAL))
        self.assertIsNone(numeros.percentual_para_fracao(None))
        self.assertIsNone(numeros.fracao_para_percentual(None))

    def test_valor_fiscal_arredonda_uma_vez_a_2_casas_half_even(self):
        self.assertEqual(str(numeros.arredondar_valor(D("2.005"))), "2.00")
        self.assertEqual(str(numeros.arredondar_valor(D("2.015"))), "2.02")
        exato = numeros.multiplicar(D("987654.32"), numeros.percentual_para_fracao(CBS_INTEGRAL))
        self.assertEqual(str(numeros.arredondar_valor(exato)), "16631.49")
        self.assertIsNone(numeros.arredondar_valor(None))

    def test_valor_fiscal_conta_exata_nao_sobe_quase_empate(self):
        # 16.451,365 x 1,3663% = 224,774999995: a conta exata fica abaixo do meio centavo.
        exato = numeros.multiplicar(
            numeros.multiplicar(D("32902.73"), D("0.5")),
            numeros.percentual_para_fracao(D("1.3663")))
        self.assertEqual(str(exato), "224.774999995")
        self.assertEqual(str(numeros.arredondar_valor(exato)), "224.77")

    def test_aliquota_publicada_arredonda_uma_vez_a_4_casas_half_even(self):
        # 8,8123% com redução de 60%: 3,52492 vira 3,5249.
        self.assertEqual(str(numeros.arredondar_aliquota_publicada(
            numeros.multiplicar(D("8.8123"), D("0.4")))), "3.5249")
        self.assertEqual(str(numeros.arredondar_aliquota_publicada(D("0.12345"))), "0.1234")
        self.assertEqual(str(numeros.arredondar_aliquota_publicada(D("0.12355"))), "0.1236")
        self.assertEqual(str(numeros.arredondar_aliquota_publicada(D("8.8"))), "8.8000")
        self.assertIsNone(numeros.arredondar_aliquota_publicada(None))

    def test_aliquota_do_leiaute_arredonda_nas_casas_do_documento(self):
        self.assertEqual(str(numeros.arredondar_aliquota(CBS_INTEGRAL, 2)), "1.68")
        self.assertEqual(str(numeros.arredondar_aliquota(CBS_INTEGRAL, None)), str(CBS_INTEGRAL))
        self.assertIsNone(numeros.arredondar_aliquota(None, 2))

    def test_totalizar_soma_exata(self):
        self.assertEqual(str(numeros.totalizar([D("2.00"), D("4.41")])), "6.41")
        self.assertEqual(numeros.totalizar([]), D(0))
        # 40 dígitos: a soma não perde nenhum
        self.assertEqual(str(numeros.totalizar(iter([D("0.1234567890123456789012345678901234567890"), D(1)]))),
                         "1.1234567890123456789012345678901234567890")

    def test_dividir_usa_34_digitos_significativos(self):
        terco = numeros.dividir(D(1), D(3))
        self.assertEqual(len(terco.as_tuple().digits), 34)
        # Dígitos significativos, não casas: um número pequeno preserva 34 dígitos.
        self.assertEqual(len(numeros.dividir(D("0.0001"), D(3)).as_tuple().digits), 34)
        self.assertIsNone(numeros.dividir(None, D(1)))

    def test_divisao_tem_34_digitos_significativos(self):
        self.assertEqual(str(numeros.dividir(D(1), D(3))), "0.3333333333333333333333333333333333")

    def test_ratear_parcelas_somam_o_total_centavos_ao_maior_resto(self):
        # 1,80 em trechos de 10, 13 e 10 de 33: 0,5454..., 0,7090... e 0,5454...
        total = D("1.80")
        extensao = D(33)
        exatas = [numeros.dividir(total * D(e), extensao) for e in ("10", "13", "10")]
        # truncadas: 0,54 + 0,70 + 0,54 = 1,78; os 2 centavos vão ao maior resto e, no empate,
        # à parcela de menor posição
        self.assertEqual(textos(numeros.ratear_parcelas(exatas)), ["0.55", "0.71", "0.54"])

        total_ibs = D("0.20")
        exatas_ibs = [numeros.dividir(total_ibs * D(e), extensao) for e in ("10", "13", "10")]
        self.assertEqual(textos(numeros.ratear_parcelas(exatas_ibs)), ["0.06", "0.08", "0.06"])

    def test_ratear_casos_de_borda(self):
        self.assertEqual(numeros.ratear_parcelas([]), [])
        self.assertEqual(textos(numeros.ratear_parcelas([D(0), D(0)])), ["0.00", "0.00"])
        # parcela única: é o próprio valor fiscal
        self.assertEqual(textos(numeros.ratear_parcelas([D("2.005")])), ["2.00"])
        # três terços de um centavo: o total é 0,01 e vai à primeira parcela
        terco = D("0.0033333333")
        self.assertEqual(textos(numeros.ratear_parcelas([terco, terco, terco])),
                         ["0.01", "0.00", "0.00"])

    def test_ratear_propriedade_soma_igual_ao_total_e_parcela_a_menos_de_um_centavo(self):
        sorteio = random.Random(20261117)
        centavo = D("0.01")
        for _ in range(2000):
            quantidade = 1 + sorteio.randrange(12)
            exatas = [D(sorteio.randrange(5_000_000)).scaleb(-6) for _ in range(quantidade)]
            parcelas = numeros.ratear_parcelas(exatas)
            self.assertEqual(numeros.arredondar_valor(numeros.totalizar(exatas)),
                             numeros.totalizar(parcelas))
            for exata, parcela in zip(exatas, parcelas):
                self.assertEqual(parcela.as_tuple().exponent, -2)
                self.assertLess(abs(parcela - exata), centavo)

    def test_formatar_moeda_nao_altera_valor_fiscal_ja_arredondado(self):
        sorteio = random.Random(20261117)
        for _ in range(2000):
            valor = numeros.arredondar_valor(D(sorteio.randrange(1_000_000_000_000)).scaleb(-5))
            self.assertEqual(valor, D(numeros.formatar_moeda(valor)))
        self.assertEqual(numeros.formatar_moeda(D("804.37")), "804.37")
        self.assertEqual(numeros.formatar_moeda(D(0)), "0.00")
        self.assertEqual(numeros.formatar_moeda(D("0E-8")), "0.00")

    def test_formatar_aliquota_apresenta_de_2_a_4_casas(self):
        self.assertEqual(numeros.formatar_aliquota(CBS_INTEGRAL), "1.6839")
        self.assertEqual(numeros.formatar_aliquota(D("0.01867380622521988789135487")), "0.0187")
        self.assertEqual(numeros.formatar_aliquota(D("2.8780")), "2.878")
        self.assertEqual(numeros.formatar_aliquota(D(0)), "0.00")
        self.assertEqual(numeros.formatar_aliquota(D("60.0000")), "60.00")

    def test_formatar_aliquota_nao_altera_aliquota_publicada(self):
        sorteio = random.Random(20261117)
        for _ in range(2000):
            aliquota = numeros.arredondar_aliquota_publicada(D(sorteio.randrange(100_000_000)).scaleb(-6))
            self.assertEqual(aliquota, D(numeros.formatar_aliquota(aliquota)))

    def test_formatar_quantidade_inteiro_ou_4_casas(self):
        self.assertEqual(numeros.formatar_quantidade(D("1000.000")), "1000")
        self.assertEqual(numeros.formatar_quantidade(D("55.55")), "55.5500")
        self.assertEqual(numeros.formatar_quantidade(D("0.0001")), "0.0001")


class ComportamentoDoPortTest(unittest.TestCase):
    """Casos que o Java cobre pela própria linguagem e o Python precisa provar."""

    def test_meio_vai_para_o_par(self):
        self.assertEqual(numeros.arredondar_valor(D("0.125")), D("0.12"))
        self.assertEqual(numeros.arredondar_valor(D("0.135")), D("0.14"))

    def test_percentual_e_fracao_sao_exatos(self):
        self.assertEqual(numeros.percentual_para_fracao(D("1.25")), D("0.0125"))
        self.assertEqual(numeros.fracao_para_percentual(D("0.006132")), D("0.6132"))

    def test_nulo_propaga(self):
        self.assertIsNone(numeros.multiplicar(None, D(1)))
        self.assertIsNone(numeros.ratear_valor(None, [D(1)]))
        self.assertIsNone(numeros.ratear_valor(D(1), None))

    def test_rateio_fecha_o_total(self):
        partes = numeros.ratear_valor(D("10.00"), [D(1), D(1), D(1)])
        self.assertEqual(sum(partes), D("10.00"))
        self.assertEqual(textos(partes), ["3.34", "3.33", "3.33"])

    def test_rateio_por_pesos_desiguais(self):
        partes = numeros.ratear_valor(D("1.80"), [D(10), D(13), D(10)])
        self.assertEqual(textos(partes), ["0.55", "0.71", "0.54"])

    def test_contexto_global_nao_interfere(self):
        import decimal
        antes = decimal.getcontext().prec
        decimal.getcontext().prec = 5
        try:
            self.assertEqual(str(numeros.multiplicar(D("0.9"), CBS_INTEGRAL)),
                             "1.51554414405505172492366586")
            self.assertEqual(str(numeros.dividir(D(1), D(3))), "0." + "3" * 34)
            self.assertEqual(numeros.formatar_aliquota(D("123456.78905")), "123456.789")
        finally:
            decimal.getcontext().prec = antes

    def test_zero_negativo_sai_sem_sinal(self):
        self.assertEqual(numeros.formatar_moeda(D("-0.001")), "0.00")
        self.assertEqual(str(numeros.arredondar_valor(D("-0.004"))), "0.00")
        self.assertEqual(numeros.formatar_aliquota(D("-0.00001")), "0.00")

    def test_nenhum_float_no_modulo(self):
        fonte = Path(numeros.__file__).read_text(encoding="utf-8")
        self.assertNotIn("float(", fonte)
        self.assertNotIn("float ", fonte)


if __name__ == "__main__":
    unittest.main()
