"""Testes da sugestão de classificação da skill cbs-ibs-simples (sugestao.py).

A sugestão é a leitura de varejo da CD2, não da Receita: fica fora da conferência com a calculadora oficial.
"""
import contextlib
import importlib.util
import io
import json
import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "core" / "cd2-super" / "skills" / "cbs-ibs-simples" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.append(str(SCRIPTS))

import simples_base  # noqa: E402
import sugestao  # noqa: E402


def _carregar_calc():
    # os testes das outras calculadoras também têm um calc.py: carrega este com o mesmo nome de test_calc_simples
    if "calc_simples" not in sys.modules:
        spec = importlib.util.spec_from_file_location("calc_simples", SCRIPTS / "calc.py")
        modulo = importlib.util.module_from_spec(spec)
        sys.modules["calc_simples"] = modulo
        spec.loader.exec_module(modulo)
    return sys.modules["calc_simples"]


calc = _carregar_calc()

DATA = date(2027, 3, 15)


class SugestaoTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = simples_base.Base()

    @classmethod
    def tearDownClass(cls):
        cls.base.fechar()

    def sugerir(self, ncm, tp_doc=65, data=DATA):
        return sugestao.sugerir(ncm, tp_doc, data, self.base)

    def codigos(self, resultado):
        return [s["cClassTrib"] for s in resultado["sugestoes"]]

    def test_arroz_sugere_cesta_basica(self):
        r = self.sugerir("10063021")
        self.assertEqual(r["ncm"], "10063021")
        self.assertEqual(self.codigos(r)[0], "200003")
        self.assertEqual(r["sugestoes"][0]["cst"], "200")
        self.assertIn("LC 214/2025, Art. 125", r["sugestoes"][0]["motivo"])

    def test_item_comum_sugere_tributacao_integral(self):
        r = self.sugerir(self.ncm_comum())
        self.assertEqual(self.codigos(r), ["000001"])
        self.assertEqual(r["sugestoes"][0]["cst"], "000")
        self.assertTrue(any("Nenhuma classificação" in a for a in r["avisos"]))

    def ncm_comum(self):
        # primeiro NCM de 8 dígitos da base que não cai em nenhuma lista nem dica do perfil
        linhas = self.base._con.execute(
            "SELECT NCM_CD FROM NCM WHERE length(NCM_CD) = 8 ORDER BY NCM_CD").fetchall()
        for (ncm,) in linhas:
            if not self.base.existe_ncm(ncm, DATA):
                continue
            r = self.sugerir(ncm)
            if self.codigos(r) == ["000001"]:
                return ncm
        self.fail("nenhum NCM comum na base")

    def test_ncm_com_pontos(self):
        r = self.sugerir("1006.30.21")
        self.assertEqual(r["ncm"], "10063021")
        self.assertEqual(self.codigos(r)[0], "200003")
        self.assertTrue(any("1006.30.21" in a and "10063021" in a for a in r["avisos"]))

    def test_ncm_com_sete_digitos_avisa_do_zero(self):
        # o brief usa "2030000", mas 02030000 não existe na base V0073: o caso feliz usa 4072100
        r = self.sugerir("4072100")
        self.assertEqual(r["ncm"], "04072100")
        self.assertTrue(any("zero à esquerda" in a and "04072100" in a for a in r["avisos"]))
        self.assertIn("200014", self.codigos(r))

    def test_sete_digitos_que_nao_existem_depois_do_zero(self):
        r = self.sugerir("2030000")
        self.assertIsNone(r["ncm"])
        self.assertEqual(r["sugestoes"], [])
        self.assertTrue(any("zero à esquerda" in a and "02030000" in a for a in r["avisos"]))
        self.assertTrue(any("não encontrado" in a for a in r["avisos"]))

    def test_ncm_que_nao_existe(self):
        r = self.sugerir("85171231")
        self.assertIsNone(r["ncm"])
        self.assertEqual(r["sugestoes"], [])
        self.assertTrue(any("não encontrado" in a for a in r["avisos"]))

    def test_ncm_vazio_ou_com_letras(self):
        for texto in ("", "   ", "abc", "1006x021", "1006.30.2a", "123", "123456789", None, 10063021):
            with self.subTest(texto=texto):
                r = self.sugerir(texto)
                self.assertIsNone(r["ncm"])
                self.assertEqual(r["sugestoes"], [])
                self.assertTrue(r["avisos"])

    def test_normalizar_ncm_concorda_com_calc(self):
        for texto in ("1006.30.21", "1006 30 21", "1006-30-21", "10063021", "1006.3021", "abc", "10063021x",
                      "1006..30.21", "1006.30.2a"):
            with self.subTest(texto=texto):
                esperado = calc.normalizar({"itens": [{"ncm": texto}]})[0]["itens"][0]["ncm"]
                ncm, _ = sugestao.normalizar_ncm(texto)
                if len(esperado) == 8 and esperado.isdigit():
                    self.assertEqual(ncm, esperado)
                else:
                    self.assertIsNone(ncm)

    def test_normalizar_ncm_sete_digitos_e_avisos(self):
        self.assertEqual(sugestao.normalizar_ncm("4072100")[0], "04072100")
        self.assertEqual(sugestao.normalizar_ncm("10063021"), ("10063021", []))
        ncm, avisos = sugestao.normalizar_ncm("1006.30.21")
        self.assertEqual(ncm, "10063021")
        self.assertEqual(len(avisos), 1)

    def test_remedio_vem_como_conferir_e_com_a_regra_geral_por_ultimo(self):
        r = self.sugerir("30049099")
        codigos = self.codigos(r)
        self.assertIn("200032", codigos)
        self.assertEqual(codigos[-1], "000001")
        dica = next(s for s in r["sugestoes"] if s["cClassTrib"] == "200032")
        self.assertIn("confirme com o contador", dica["motivo"])

    def test_listas_com_reducoes_diferentes_avisam(self):
        # 200003 (100%) e 200034 (60%) cobrem o mesmo NCM na base
        achou = None
        for (ncm,) in self.base._con.execute(
                "SELECT NCM_CD FROM NCM WHERE length(NCM_CD) = 8").fetchall():
            if self.base.existe_ncm(ncm, DATA):
                codigos = self.codigos(self.sugerir(ncm))
                if "200003" in codigos and "200034" in codigos:
                    achou = (ncm, codigos)
                    break
        self.assertIsNotNone(achou)
        r = self.sugerir(achou[0])
        self.assertLess(self.codigos(r).index("200003"), self.codigos(r).index("200034"))
        self.assertTrue(any("reduções diferentes" in a for a in r["avisos"]))

    def test_documento_fora_do_escopo_ou_data_sem_calculo(self):
        for tp_doc, data in ((57, DATA), (65, date(2026, 11, 22)), (99, DATA)):
            with self.subTest(tp_doc=tp_doc, data=data):
                r = self.sugerir("10063021", tp_doc, data)
                self.assertEqual(r["sugestoes"], [])
                self.assertTrue(r["avisos"])

    def test_nfe_tambem_sugere(self):
        self.assertEqual(self.codigos(self.sugerir("10063021", 55))[0], "200003")

    def test_toda_sugestao_e_aceita_pela_base(self):
        sigla = self.base.sigla_tipo_dfe_vigente(self.base.tipo_dfe_calculo(65, DATA)["tipoDfeId"], DATA)
        todos = [n for (n,) in self.base._con.execute(
            "SELECT NCM_CD FROM NCM WHERE length(NCM_CD) = 8 ORDER BY NCM_CD").fetchall()
            if self.base.existe_ncm(n, DATA)]
        self.assertGreater(len(todos), 1000)
        # a amostra regular não garante os casos com benefício: 40 NCM que o perfil sugere, mais comuns até 200
        com_beneficio = [n for n in todos if len(self.sugerir(n)["sugestoes"]) > 1][:40]
        comuns = [n for n in todos[::len(todos) // 400] if n not in com_beneficio]
        amostra = sorted(com_beneficio + comuns[:200 - len(com_beneficio)])
        self.assertEqual(len(amostra), 200)
        for ncm in amostra:
            r = self.sugerir(ncm)
            self.assertTrue(r["sugestoes"], ncm)
            for s in r["sugestoes"]:
                classificacao = self.base.classificacao_cbs_ibs(s["cClassTrib"], DATA)
                with self.subTest(ncm=ncm, codigo=s["cClassTrib"]):
                    self.assertTrue(self.base.validar_ncm(ncm, classificacao["id"], s["cClassTrib"], DATA,
                                                          "CBS e IBS"))
                    self.assertIsNone(self.base.validar_nomenclatura(
                        sigla, classificacao["id"], s["cClassTrib"], ncm, None, DATA, "CBS e IBS"))
                    self.assertEqual(s["cst"], classificacao["cst"])

    def test_todo_codigo_do_perfil_existe_na_base(self):
        # quebra quando a Receita tira uma classificação, ou deixa de aceitar NFC-e e NF-e
        for codigo, perfil in sugestao.PERFIL["classificacoes"].items():
            with self.subTest(codigo=codigo):
                classificacao = self.base.classificacao_cbs_ibs(codigo, DATA)
                siglas = {simples_base.normalizar_sigla(t["tipoDfe"]["sigla"])
                          for t in self.base.tipo_dfe_classificacao(classificacao["id"], DATA)}
                self.assertIn("NFCE", siglas)
                self.assertIn("NFE", siglas)
                tem_lista = self.base.tem_ncm_aplicavel_para_classificacao(classificacao["id"], DATA)
                self.assertEqual(tem_lista, perfil["tipo"] == "lista")
                self.assertTrue(perfil["referencia"].startswith("LC 214/2025"))
        self.assertIn(sugestao.PERFIL["geral"], sugestao.PERFIL["classificacoes"])
        for dica in sugestao.PERFIL["dicas"]:
            for codigo in dica["codigos"]:
                self.assertEqual(sugestao.PERFIL["classificacoes"][codigo]["tipo"], "dica")

    def test_origem_e_sempre_leitura_cd2(self):
        for ncm in ("10063021", "85171231", "", "30049099", "4072100"):
            with self.subTest(ncm=ncm):
                self.assertEqual(self.sugerir(ncm)["origem"], "leitura-cd2")

    def test_formato_da_sugestao(self):
        for s in self.sugerir("10063021")["sugestoes"]:
            self.assertEqual(set(s), {"cClassTrib", "cst", "descricao", "motivo"})


class LinhaDeComandoTest(unittest.TestCase):
    def rodar(self, *argv):
        saida, erro = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(saida), contextlib.redirect_stderr(erro):
            try:
                codigo = sugestao.principal(list(argv))
            except SystemExit as fim:
                codigo = fim.code
        return codigo, saida.getvalue(), erro.getvalue()

    def test_saida_traz_o_aviso_fixo_e_a_origem(self):
        codigo, saida, _ = self.rodar("--ncm", "1006.30.21", "--tp-doc", "65", "--data", "2027-03-15")
        self.assertEqual(codigo, 0)
        corpo = json.loads(saida)
        self.assertEqual(corpo["aviso"], "Classificação sugerida pela CD2, confirme com o contador.")
        self.assertEqual(corpo["origem"], "leitura-cd2")
        self.assertEqual(corpo["sugestoes"][0]["cClassTrib"], "200003")

    def test_data_invalida_e_data_ausente(self):
        for argv in (("--ncm", "10063021", "--data", "15/03/2027"), ("--ncm", "10063021")):
            with self.subTest(argv=argv):
                codigo, _, erro = self.rodar(*argv)
                self.assertEqual(codigo, 2)
                self.assertTrue(erro)


if __name__ == "__main__":
    unittest.main()
