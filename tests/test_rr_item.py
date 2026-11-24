"""Testes do processamento do item (rr_item) e da base de cálculo pelos componentes (rr_base_calculo).

A paridade com as respostas oficiais fica no test_calc_rr (grupo nomenclatura). Aqui ficam os caminhos
que a bateria não cobre: o gComponentesBC (nenhum caso gravado o traz), o ajuste do NCM zerado e o que
o ItemProcessado leva para o cálculo.
"""
import importlib.util
import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "core" / "cd2-super" / "skills" / "cbs-ibs-regime-regular" / "scripts"


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


rr_erros = load_module("rr_erros", SCRIPTS / "rr_erros.py")
rr_entrada = load_module("rr_entrada", SCRIPTS / "rr_entrada.py")
rr_base = load_module("rr_base", SCRIPTS / "rr_base.py")
rr_base_calculo = load_module("rr_base_calculo", SCRIPTS / "rr_base_calculo.py")
rr_item = load_module("rr_item", SCRIPTS / "rr_item.py")

D = Decimal
DATA = date(2026, 6, 1)


def _componentes(**valores):
    c = rr_entrada.ComponentesBaseCalculo()
    for campo, valor in valores.items():
        setattr(c, campo, D(valor))
    return c


class BaseCalculoTest(unittest.TestCase):
    def test_tp_doc_fora_de_55_e_65(self):
        for tp_doc in (None, 57):
            with self.assertRaises(rr_erros.ErroRR) as ctx:
                rr_base_calculo.calcular(_componentes(v_prod="10"), 2026, None, tp_doc)
            self.assertEqual(ctx.exception.codigo, "REG-024")
            self.assertEqual(ctx.exception.status_http, 400)
            self.assertEqual(ctx.exception.detalhe, "Tipo de documento deve ser 55 (NF-e) ou 65 (NFC-e)")

    def test_soma_e_subtrai_por_base(self):
        c = _componentes(v_prod="100.00", v_frete="10", v_desc="5.5", v_pis="1.65", v_icms="12", v_is="3")
        r = rr_base_calculo.calcular(c, 2026, rr_base_calculo.BEM, 55)
        # 2026: vIS fora (vigência 2027-2033); vPIS e vICMS (bem) subtraem RG e IS, não SN
        self.assertEqual((str(r.base_rg), str(r.base_is), str(r.base_sn)), ("90.85", "90.85", "104.50"))
        self.assertEqual(r.avisos, ["Campo vIS não compõe as bases para o ano 2026 (vigência 2027\u20132033) e "
                                    "foi ignorado no cálculo"])
        r = rr_base_calculo.calcular(c, 2027, None, 65)
        # 2027: vPIS fora; vICMS só para bem (natureza nula não serve); vIS soma RG e SN
        self.assertEqual((str(r.base_rg), str(r.base_is), str(r.base_sn)), ("107.50", "104.50", "107.50"))

    def test_modelo_do_documento(self):
        r = rr_base_calculo.calcular(_componentes(v_prod="1", v_ii="2"), 2026, rr_base_calculo.BEM, 65)
        self.assertEqual(r.base_rg, D("1"))
        self.assertEqual(r.avisos, ["Campo vII não compõe as bases para o documento modelo 65 e foi ignorado no "
                                    "cálculo"])


class ProcessamentoTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = rr_base.Base()

    @classmethod
    def tearDownClass(cls):
        cls.base.fechar()

    def _op(self, item, tp_doc=None):
        return rr_entrada.Operacao(municipio=4314902, uf="RS", tp_doc=tp_doc, itens=[item],
                                   dh_fato_gerador=rr_entrada.DataHora("2026-06-01T10:00:00-03:00", DATA))

    def _processar(self, item, tp_doc=None):
        op = self._op(item, tp_doc)
        tipo = None if tp_doc is None else self.base.buscar_por_tipo(tp_doc, DATA)
        return rr_item.processar_item(item, op, tipo, self.base, DATA)

    def test_item_simples_chega_ao_calculo_com_os_padroes(self):
        item = rr_entrada.Item(numero=1, cst="000", c_class_trib="000001", base_calculo=D("100"))
        proc = self._processar(item)
        self.assertEqual((proc.quantidade, proc.unidade), (D("1"), "UN"))
        self.assertEqual(proc.base_calculo, D("100"))
        self.assertIsNone(proc.tratamento_is)
        self.assertIsNone(proc.aliquota_is)
        self.assertFalse(proc.tem_desoneracao)
        self.assertEqual(proc.tratamento_cbs_ibs, proc.tratamento_efetivo)
        self.assertEqual(proc.codigo_uf, self.base.consultar_por_sigla("RS"))
        self.assertEqual(proc.p_redutor, D(0))
        self.assertFalse(proc.calculo_simulado)

    def test_componentes_preenchem_a_base(self):
        item = rr_entrada.Item(numero=1, ncm="22030000", cst="000", c_class_trib="000001",
                               g_componentes_bc=_componentes(v_prod="100.00", v_desc="0.004"))
        proc = self._processar(item, tp_doc=55)
        self.assertEqual(str(proc.base_calculo), "99.996")

    def test_componentes_sem_tp_doc(self):
        item = rr_entrada.Item(numero=1, cst="000", c_class_trib="000001",
                               g_componentes_bc=_componentes(v_prod="100"))
        with self.assertRaises(rr_erros.ErroRR) as ctx:
            self._processar(item)
        self.assertEqual(ctx.exception.codigo, "REG-024")

    def test_componentes_divergem_da_base_declarada(self):
        item = rr_entrada.Item(numero=1, cst="000", c_class_trib="000001", base_calculo=D("100"),
                               g_componentes_bc=_componentes(v_prod="100.00", v_desc="0.004"))
        with self.assertRaises(rr_erros.ErroRR) as ctx:
            self._processar(item, tp_doc=55)
        self.assertEqual(ctx.exception.codigo, "REG-034")
        self.assertEqual(ctx.exception.detalhe, "A base de cálculo declarada (100.00) diverge da base calculada a "
                                                "partir dos componentes informados (99.996)")

    def test_componentes_negativos(self):
        item = rr_entrada.Item(numero=1, cst="000", c_class_trib="000001",
                               g_componentes_bc=_componentes(v_prod="1", v_desc="2"))
        with self.assertRaises(rr_erros.ErroRR) as ctx:
            self._processar(item, tp_doc=55)
        self.assertEqual((ctx.exception.codigo, ctx.exception.detalhe),
                         ("REG-023", "A base de cálculo calculada a partir dos componentes é negativa"))

    def test_ncm_zerado_some_no_documento_que_permite(self):
        tipo = self.base.buscar_por_tipo(55, DATA)
        item = rr_entrada.Item(numero=1, ncm="00000000", cst="000", c_class_trib="000001", base_calculo=D("1"))
        proc = self._processar(item, tp_doc=55)
        self.assertEqual(proc.ncm, None if tipo["permiteNcmZero"] else "00000000")

    def test_nbs_fica_fora_do_escopo(self):
        item = rr_entrada.Item(numero=1, nbs="101011100", cst="000", c_class_trib="000001", base_calculo=D("1"))
        with self.assertRaises(rr_item.ForaDoEscopo):
            self._processar(item)


if __name__ == "__main__":
    unittest.main()
