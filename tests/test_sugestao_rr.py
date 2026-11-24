"""Testes da sugestão de classificação da skill cbs-ibs-regime-regular (sugestao.py).

A sugestão é a leitura de varejo da CD2, não da Receita: fica fora da conferência com a calculadora oficial.
As amostras saem da própria base da skill, para os testes seguirem valendo quando o robô trocar as tabelas.
"""
import contextlib
import importlib.util
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "core" / "cd2-super" / "skills" / "cbs-ibs-regime-regular" / "scripts"
# A pasta de scripts do RR tem um sugestao.py, como a do Simples: o teste do Simples faz `import sugestao`.
# Por isso esta pasta não pode ficar no sys.path depois da carga, se ela não estava lá antes.
_JA_NO_CAMINHO = str(SCRIPTS) in sys.path


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


rr_base = load_module("rr_base", SCRIPTS / "rr_base.py")
sugestao = load_module("rr_sugestao", SCRIPTS / "sugestao.py")
if not _JA_NO_CAMINHO:
    sys.path[:] = [p for p in sys.path if p != str(SCRIPTS)]

DATA = date(2027, 3, 15)
TRAVESSOES = ("—", "–")


class SugestaoRRTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = rr_base.Base()
        cls._cache = {}

    @classmethod
    def tearDownClass(cls):
        cls.base.fechar()

    # ------------------------------------------------------------ amostras da base

    def sugerir(self, ncm, tp_doc=65, data=DATA):
        return sugestao.sugerir(ncm, tp_doc, data, self.base)

    def codigos(self, ncm):
        return [s["cClassTrib"] for s in self.sugerir(ncm)["sugestoes"]]

    def vigentes(self):
        if "vigentes" not in self._cache:
            self._cache["vigentes"] = [n for (n,) in self.base._con.execute(
                "SELECT NCM_CD FROM NCM WHERE length(NCM_CD) = 8 ORDER BY NCM_CD").fetchall()
                if self.base.existe_ncm(n, DATA)]
        return self._cache["vigentes"]

    def arroz(self):
        """O NCM do arroz (1006...) que cai na lista da cesta básica (200003), tirado da base."""
        if "arroz" not in self._cache:
            self._cache["arroz"] = next(n for n in self.vigentes() if n.startswith("1006")
                                        and self.codigos(n) == ["200003", "000001"])
        return self._cache["arroz"]

    def com_imposto_seletivo(self, quantos=3):
        if "is" not in self._cache:
            linhas = self.base._con.execute(
                "SELECT DISTINCT AAVP_NCM_CD FROM ALIQUOTA_AD_VALOREM_PRODUTO WHERE length(AAVP_NCM_CD) = 8 "
                "ORDER BY AAVP_NCM_CD").fetchall()
            self._cache["is"] = [n for (n,) in linhas if self.base.existe_ncm(n, DATA)
                                 and self.sugerir(n)["imposto_seletivo"]["na_lista"]]
        return self._cache["is"][:quantos]

    def sem_imposto_seletivo(self):
        return next(n for n in self.vigentes() if not self.sugerir(n)["imposto_seletivo"]["na_lista"])

    def sete_digitos(self):
        """Um NCM vigente que começa por zero e tem sugestão com lista, escrito sem o zero."""
        if "sete" not in self._cache:
            self._cache["sete"] = next(n for n in self.vigentes() if n.startswith("0") and len(self.codigos(n)) > 1)
        return self._cache["sete"]

    def sete_digitos_inexistente(self):
        n = 2030000
        while self.base.existe_ncm(f"0{n:07d}", DATA):
            n += 1
        return f"{n:07d}"

    def reducao_da_base(self, codigo, tributo=2):
        return self.base._con.execute(
            "SELECT PERE_VALOR FROM PERCENTUAL_REDUCAO p JOIN CLASSIFICACAO_TRIBUTARIA c ON c.CLTR_ID = p.PERE_CLTR_ID "
            "WHERE c.CLTR_CD = ? AND p.PERE_TBTO_ID = ?", (codigo, tributo)).fetchone()[0]

    # ------------------------------------------------------------ o que a brief pede

    def test_arroz_sugere_a_classificacao_de_reducao_de_100(self):
        ncm = self.arroz()
        r = self.sugerir(ncm)
        self.assertEqual(r["ncm"], ncm)
        primeira = r["sugestoes"][0]
        self.assertEqual(primeira["cClassTrib"], "200003")
        self.assertEqual(primeira["cst"], "200")
        self.assertEqual(primeira["reducao"], "100")
        self.assertEqual(float(self.reducao_da_base("200003")), 100.0)
        self.assertIn("LC 214/2025, Art. 125", primeira["motivo"])
        self.assertIn("aplica essa redução", primeira["motivo"])

    def test_ncm_com_imposto_seletivo_diz_que_tem(self):
        amostra = self.com_imposto_seletivo(3)
        self.assertTrue(amostra)
        for ncm in amostra:
            with self.subTest(ncm=ncm):
                seletivo = self.sugerir(ncm)["imposto_seletivo"]
                self.assertTrue(seletivo["na_lista"])
                self.assertIn("tem Imposto Seletivo", seletivo["texto"])
                self.assertIn("aliquotas.imposto_seletivo", seletivo["texto"])
                self.assertIn(self.base.versao_base()["versao"], seletivo["texto"])
                self.assertIn("contador", seletivo["texto"])

    def test_ncm_sem_imposto_seletivo_diz_que_nao_tem(self):
        seletivo = self.sugerir(self.sem_imposto_seletivo())["imposto_seletivo"]
        self.assertFalse(seletivo["na_lista"])
        self.assertIn("não tem Imposto Seletivo", seletivo["texto"])

    def test_ncm_de_sete_digitos_aponta_o_de_oito_com_zero(self):
        ncm = self.sete_digitos()
        r = self.sugerir(ncm[1:])
        self.assertEqual(r["ncm"], ncm)
        self.assertTrue(any("zero à esquerda" in a and ncm in a for a in r["avisos"]))
        self.assertTrue(any("calc.py não completa o zero" in a for a in r["avisos"]))
        self.assertEqual([s["cClassTrib"] for s in r["sugestoes"]], self.codigos(ncm))

    def test_ncm_com_pontos_aponta_o_ncm_so_com_digitos(self):
        ncm = self.arroz()
        com_pontos = f"{ncm[:4]}.{ncm[4:6]}.{ncm[6:]}"
        r = self.sugerir(com_pontos)
        self.assertEqual(r["ncm"], ncm)
        self.assertEqual(r["sugestoes"][0]["cClassTrib"], "200003")
        self.assertTrue(any(com_pontos in a and ncm in a and "calc.py" in a for a in r["avisos"]))

    def test_sete_digitos_que_nao_existem_depois_do_zero(self):
        sete = self.sete_digitos_inexistente()
        r = self.sugerir(sete)
        self.assertIsNone(r["ncm"])
        self.assertEqual(r["sugestoes"], [])
        self.assertTrue(any("zero à esquerda" in a and "0" + sete in a for a in r["avisos"]))
        self.assertTrue(any("não encontrado" in a for a in r["avisos"]))

    def test_ncm_vazio_ou_com_letras(self):
        for texto in ("", "   ", "abc", "1006x021", "1006.30.2a", "123", "123456789", None, 10063021):
            with self.subTest(texto=texto):
                r = self.sugerir(texto)
                self.assertIsNone(r["ncm"])
                self.assertEqual(r["sugestoes"], [])
                self.assertTrue(r["avisos"])

    def test_nenhum_texto_diz_que_a_reducao_nao_tem_efeito(self):
        # o texto do Simples ("a redução não tem efeito") nunca pode sair aqui: no regime regular ela vale
        ncms = [self.arroz(), self.sete_digitos()[1:], self.sete_digitos_inexistente(),
                *self.com_imposto_seletivo(2), "30049099", "", "abc"]
        ncms += self.vigentes()[::max(1, len(self.vigentes()) // 150)]
        proibidos = ("não tem efeito", "sem efeito", "não baixa", "Simples", "PGDAS", "efeito_no_simples")
        for ncm in ncms:
            for tp_doc in (65, 55, 57, 99):
                texto = json.dumps(self.sugerir(ncm, tp_doc), ensure_ascii=False)
                for frase in proibidos:
                    self.assertNotIn(frase, texto, (ncm, tp_doc))
                for t in TRAVESSOES:
                    self.assertNotIn(t, texto)
        fonte = (SCRIPTS / "sugestao.py").read_text(encoding="utf-8")
        for frase in ("não tem efeito", "sem efeito", "Simples", "PGDAS", "_efeito", "_tp_rbsn"):
            self.assertNotIn(frase, fonte)
        for t in TRAVESSOES:
            self.assertNotIn(t, fonte)

    # ------------------------------------------------------------ formato e regras

    def test_formato_da_sugestao_sem_os_campos_do_simples(self):
        r = self.sugerir(self.arroz())
        self.assertEqual(set(r), {"ncm", "avisos", "leitura", "sugestoes", "origem", "imposto_seletivo"})
        for s in r["sugestoes"]:
            self.assertEqual(set(s), {"cClassTrib", "cst", "descricao", "reducao", "motivo"})
        self.assertEqual(r["origem"], "leitura-cd2")

    def test_item_comum_sugere_tributacao_integral(self):
        ncm = next(n for n in self.vigentes() if self.codigos(n) == ["000001"])
        r = self.sugerir(ncm)
        self.assertEqual(r["sugestoes"][0]["cst"], "000")
        self.assertEqual(r["sugestoes"][0]["reducao"], "0")
        self.assertIn("nenhuma lista do perfil de varejo da CD2", r["sugestoes"][0]["motivo"])
        self.assertTrue(any("Nenhuma classificação" in a for a in r["avisos"]))

    def test_regra_geral_fica_por_ultimo_com_reducao_zero(self):
        for ncm in (self.arroz(), *self.com_imposto_seletivo(1), self.sete_digitos()):
            ultima = self.sugerir(ncm)["sugestoes"][-1]
            self.assertEqual((ultima["cClassTrib"], ultima["reducao"]), ("000001", "0"))

    def test_listas_saem_da_menor_reducao_para_a_maior(self):
        achou = 0
        for ncm in self.vigentes():
            r = self.sugerir(ncm)
            listas = [s for s in r["sugestoes"]
                      if sugestao.PERFIL["classificacoes"][s["cClassTrib"]]["tipo"] == "lista"]
            if len(listas) >= 2:
                achou += 1
                reducoes = [float(s["reducao"]) for s in listas]
                self.assertEqual(reducoes, sorted(reducoes), ncm)
                self.assertTrue(any("mais de uma lista" in a and "a escolha muda o valor" in a
                                    for a in r["avisos"]), ncm)
            if achou == 10:
                break
        self.assertGreater(achou, 0)

    def test_toda_sugestao_e_aceita_pela_base(self):
        tipo = self.base.buscar_por_tipo(65, DATA)
        amostra = self.vigentes()[::max(1, len(self.vigentes()) // 200)]
        for ncm in amostra:
            r = self.sugerir(ncm)
            self.assertTrue(r["sugestoes"], ncm)
            for s in r["sugestoes"]:
                with self.subTest(ncm=ncm, codigo=s["cClassTrib"]):
                    classificacao = self.base.buscar_classificacao_tributaria(s["cClassTrib"], 2, DATA)
                    self.assertEqual(s["cst"], classificacao["cst"])
                    self.assertTrue(any(v["tipoDfe"]["id"] == tipo["id"] for v in
                                        self.base.buscar_tipo_dfe_classificacao(classificacao["id"], DATA)))
                    if self.base.tem_ncm_aplicavel_para_classificacao(classificacao["id"], DATA):
                        self.assertTrue(self.base.tem_ncm_aplicavel(ncm, classificacao["id"], DATA))

    def test_todo_codigo_do_perfil_existe_na_base(self):
        for codigo, perfil in sugestao.PERFIL["classificacoes"].items():
            with self.subTest(codigo=codigo):
                classificacao = self.base.buscar_classificacao_tributaria(codigo, 2, DATA)
                self.assertIsNotNone(classificacao)
                siglas = {rr_base.normalizar_sigla(t["tipoDfe"]["sigla"])
                          for t in self.base.buscar_tipo_dfe_classificacao(classificacao["id"], DATA)}
                self.assertIn("NFCE", siglas)
                self.assertIn("NFE", siglas)
                tem_lista = self.base.tem_ncm_aplicavel_para_classificacao(classificacao["id"], DATA)
                self.assertEqual(tem_lista, perfil["tipo"] == "lista")
                self.assertTrue(perfil["referencia"].startswith("LC 214/2025"))

    def test_perfil_nao_guarda_copia_de_dado_da_base(self):
        for perfil in sugestao.PERFIL["classificacoes"].values():
            self.assertNotIn("reducao", perfil)
        self.assertNotIn("imposto_seletivo", sugestao.PERFIL)

    def test_reducao_vem_da_base_na_data(self):
        for codigo in ("200003", "200034"):
            with self.subTest(codigo=codigo):
                ncm = next((n for n in self.vigentes() if codigo in self.codigos(n)), None)
                if ncm is None:
                    self.skipTest(f"nenhum NCM da base na lista {codigo}")
                s = next(s for s in self.sugerir(ncm)["sugestoes"] if s["cClassTrib"] == codigo)
                self.assertEqual(float(s["reducao"]), float(self.reducao_da_base(codigo)))

    def test_documento_fora_do_escopo_ou_inexistente(self):
        for tp_doc in (57, 99, True, "65"):
            with self.subTest(tp_doc=tp_doc):
                r = self.sugerir(self.arroz(), tp_doc)
                self.assertEqual(r["sugestoes"], [])
                self.assertTrue(r["avisos"])

    def test_nfe_tambem_sugere(self):
        self.assertEqual(self.sugerir(self.arroz(), 55)["sugestoes"][0]["cClassTrib"], "200003")

    def test_leitura_explica_a_ordem_e_diz_que_a_reducao_vale(self):
        r = self.sugerir(self.arroz())
        self.assertIn("primeira sugestão é a mais provável", r["leitura"])
        self.assertIn("vale na conta", r["leitura"])
        self.assertNotIn("mais provável", self.sugerir(self.sete_digitos_inexistente())["leitura"])


class LinhaDeComandoTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        t = SugestaoRRTest("test_nfe_tambem_sugere")
        SugestaoRRTest.setUpClass()
        try:
            cls.ncm = t.arroz()
        finally:
            SugestaoRRTest.tearDownClass()

    def rodar(self, *argv):
        saida, erro = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(saida), contextlib.redirect_stderr(erro):
            try:
                codigo = sugestao.principal(list(argv))
            except SystemExit as fim:
                codigo = fim.code
        return codigo, saida.getvalue(), erro.getvalue()

    def test_roda_de_pasta_achatada_com_a_base_ao_lado(self):
        # o pacote do GPT achata a skill numa pasta só: script e base lado a lado
        with tempfile.TemporaryDirectory() as pasta:
            for nome in ("sugestao.py", "rr_base.py"):
                shutil.copy(SCRIPTS / nome, Path(pasta) / nome)
            shutil.copy(SCRIPTS.parent / "references" / rr_base.NOME_BASE, Path(pasta) / rr_base.NOME_BASE)
            processo = subprocess.run(
                [sys.executable, "-I", str(Path(pasta) / "sugestao.py"), "--ncm", self.ncm, "--data", "2027-03-15"],
                capture_output=True, text=True, cwd=pasta)
        self.assertEqual(processo.returncode, 0, processo.stderr)
        self.assertEqual(json.loads(processo.stdout)["sugestoes"][0]["cClassTrib"], "200003")

    def test_saida_traz_o_aviso_fixo_e_a_origem(self):
        com_pontos = f"{self.ncm[:4]}.{self.ncm[4:6]}.{self.ncm[6:]}"
        codigo, saida, _ = self.rodar("--ncm", com_pontos, "--tp-doc", "65", "--data", "2027-03-15")
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
