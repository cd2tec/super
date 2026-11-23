"""Testes da sugestão de classificação da skill cbs-ibs-simples (sugestao.py).

A sugestão é a leitura de varejo da CD2, não da Receita: fica fora da conferência com a calculadora oficial.
"""
import contextlib
import importlib.util
import io
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from decimal import Decimal
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

    def test_duas_listas_saem_da_menor_reducao_para_a_maior(self):
        # evidência da revisão: NFC-e em 2027-03-15
        esperado = {
            "07108000": ["200034", "200014"], "21069090": ["200033", "200003"],
            "25010090": ["200033", "200003"], "15132120": ["200034", "200003"],
            "90182010": ["200030", "200004"], "90189010": ["200030", "200004"],
            "90189099": ["200030", "200004"], "90211010": ["200030", "200004"],
            "90211020": ["200030", "200004"],
            "22029900": ["200033", "200034"],  # empate em 60%: desempate pelo código
        }
        for ncm, listas in esperado.items():
            with self.subTest(ncm=ncm):
                r = self.sugerir(ncm)
                self.assertEqual(self.codigos(r), listas + ["000001"])
                reducoes = [int(s["reducao"]) for s in r["sugestoes"][:-1]]
                self.assertEqual(reducoes, sorted(reducoes))
                for s in r["sugestoes"][:-1]:
                    self.assertIn(f"Redução de {s['reducao']}%", s["motivo"])
                aviso = next(a for a in r["avisos"] if "mais de uma lista" in a)
                self.assertIn("nunca fica abaixo do real", aviso)
                self.assertIn("natureza do seu produto", aviso)

    def test_uma_lista_so_nao_avisa_de_varias_listas(self):
        self.assertFalse(any("mais de uma lista" in a for a in self.sugerir("10063021")["avisos"]))

    def test_regra_geral_fica_sempre_por_ultimo_com_reducao_zero(self):
        for ncm in ("10063021", "07108000", "30049099", "22030000"):
            ultima = self.sugerir(ncm)["sugestoes"][-1]
            self.assertEqual((ultima["cClassTrib"], ultima["reducao"]), ("000001", "0"))

    def test_remedio_lista_vem_antes_da_dica_e_a_dica_que_promete_mais_avisa(self):
        # como no mini app: a lista que o NCM cumpre (200030) fica na frente, a dica depois
        r = self.sugerir("30049099")
        self.assertEqual(self.codigos(r), ["200030", "200032", "200009", "000001"])
        self.assertTrue(any("200009" in a and "mais redução" in a for a in r["avisos"]))

    def test_reducao_lida_da_base_bate_com_a_do_perfil_antigo_na_v0073(self):
        # Âncora de regressão: os valores que o perfil copiava do mini app quando as reduções eram texto fixo.
        # Só vale para a V0073. Em outra versão o teste é pulado e nunca barra uma atualização de tabelas.
        versao = self.base.versao_base()["versao"]
        if versao != "V0073":
            self.skipTest(f"âncora da V0073; a base está na {versao}, a redução vem da base e não é conferida aqui")
        antigo = {"000001": 0, "200003": 100, "200014": 100, "200034": 60, "200035": 60, "200004": 100,
                  "200030": 60, "200007": 100, "200031": 60, "200033": 60, "200032": 60, "200009": 100,
                  "200013": 100}
        self.assertEqual(set(antigo), set(sugestao.PERFIL["classificacoes"]))
        for codigo, esperado in antigo.items():
            with self.subTest(codigo=codigo):
                id_classificacao = self.base.classificacao_cbs_ibs(codigo, DATA)["id"]
                lido = self.base.percentuais_reducao(id_classificacao, DATA)
                self.assertEqual(lido, {"cbs": Decimal(esperado), "ibsUf": Decimal(esperado),
                                        "ibsMun": Decimal(esperado)})

    def test_perfil_nao_guarda_copia_de_dado_da_base(self):
        for perfil in sugestao.PERFIL["classificacoes"].values():
            self.assertNotIn("reducao", perfil)
        self.assertNotIn("imposto_seletivo", sugestao.PERFIL)

    def test_tabelas_atualizadas_pelo_robo_chegam_na_sugestao(self):
        # a situação do robô: a base nova traz outra versão e outra redução, e nenhum teste precisa mudar
        with tempfile.TemporaryDirectory() as pasta:
            destino = Path(pasta) / "base-receita.db"
            shutil.copy(SCRIPTS.parent / "references" / simples_base.NOME_BASE, destino)
            con = sqlite3.connect(destino)
            cesta = con.execute("SELECT CLTR_ID FROM CLASSIFICACAO_TRIBUTARIA WHERE CLTR_CD = '200003' "
                                "AND CLTR_NOMENCLATURA = 'NCM' AND CLTR_INICIO_VIGENCIA = '2026-01-01'").fetchone()[0]
            con.execute("UPDATE PERCENTUAL_REDUCAO SET PERE_VALOR = 70 WHERE PERE_CLTR_ID = ? AND PERE_TBTO_ID = 2",
                        (cesta,))
            con.execute("UPDATE PERCENTUAL_REDUCAO SET PERE_VALOR = 40.5 WHERE PERE_CLTR_ID = ? AND PERE_TBTO_ID = 3",
                        (cesta,))
            con.execute("INSERT INTO VERSAO_BASE_DADO (VRBD_ID, VRBD_DATA, VRBD_DESCRICAO, VRBD_VERSAO_BASE_DADO) "
                        "VALUES (9998, '2027-01-02 00:00:00', 'teste do robô', 'V9998')")
            con.commit()
            con.close()
            with simples_base.Base(destino) as nova:
                r = sugestao.sugerir("10063021", 65, DATA, nova)
                self.assertEqual(r["sugestoes"][0]["cClassTrib"], "200003")
                self.assertEqual(r["sugestoes"][0]["reducao"], "70")
                self.assertEqual(r["sugestoes"][0]["reducoes"],
                                 {"cbs": "70", "ibsEstadual": "40.5", "ibsMunicipal": "100"})
                self.assertIn("70% na CBS", r["sugestoes"][0]["motivo"])
                self.assertIn("V9998", sugestao.sugerir("22030000", 65, DATA, nova)["imposto_seletivo"]["texto"])
        # a base original continua como estava
        self.assertEqual(self.sugerir("10063021")["sugestoes"][0]["reducao"], "100")
        self.assertNotIn("reducoes", self.sugerir("10063021")["sugestoes"][0])

    def test_imposto_seletivo_na_lista(self):
        for ncm in ("22030000", "22021000", "24022000"):
            with self.subTest(ncm=ncm):
                r = self.sugerir(ncm)
                self.assertEqual(self.codigos(r), ["000001"])
                seletivo = r["imposto_seletivo"]
                self.assertTrue(seletivo["na_lista"])
                self.assertIn("valorImpostoSeletivo", seletivo["texto"])
                self.assertIn("não calcula", seletivo["texto"])
                self.assertIn("contador", seletivo["texto"])
                self.assertIn("2027", seletivo["texto"])
                self.assertIn(self.base.versao_base()["versao"], seletivo["texto"])

    def test_imposto_seletivo_fora_da_lista(self):
        # 22011000 (água) não está na lista que o mini app copia da Receita, ao contrário do que a revisão supôs
        for ncm in ("22011000", "10063021"):
            with self.subTest(ncm=ncm):
                seletivo = self.sugerir(ncm)["imposto_seletivo"]
                self.assertFalse(seletivo["na_lista"])
                self.assertIn("não verifica o Imposto Seletivo além", seletivo["texto"])

    def test_imposto_seletivo_vem_da_base_com_a_excecao_da_tabela(self):
        # 88021100 tem exceção cadastrada (88026000), que a base expressa em EXCECAO_AD_VALOREM_PRODUTO
        for ncm, esperado in (("88021100", True), ("88029999", True), ("22011000", False)):
            with self.subTest(ncm=ncm):
                self.assertEqual(self.base.no_imposto_seletivo(ncm, DATA), esperado)
        self.assertTrue(self.sugerir("22030000")["imposto_seletivo"]["na_lista"])

    def test_sem_ncm_valido_nao_ha_imposto_seletivo(self):
        self.assertNotIn("imposto_seletivo", self.sugerir("85171231"))

    def test_leitura_explica_a_ordem(self):
        r = self.sugerir("10063021")
        self.assertIn("primeira sugestão é a mais provável", r["leitura"])
        self.assertIn("000001", r["leitura"])
        self.assertNotIn("mais provável", self.sugerir("85171231")["leitura"])

    def test_texto_do_item_comum_cita_o_perfil_de_varejo(self):
        motivo = self.sugerir(self.ncm_comum())["sugestoes"][0]["motivo"]
        self.assertIn("nenhuma lista do perfil de varejo da CD2", motivo)

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
            self.assertEqual(set(s), {"cClassTrib", "cst", "descricao", "reducao", "motivo"})


class LinhaDeComandoTest(unittest.TestCase):
    def test_roda_de_pasta_achatada_com_a_base_ao_lado(self):
        # o pacote do GPT achata a skill numa pasta só: script e base lado a lado
        with tempfile.TemporaryDirectory() as pasta:
            for nome in ("sugestao.py", "simples_base.py"):
                shutil.copy(SCRIPTS / nome, Path(pasta) / nome)
            shutil.copy(SCRIPTS.parent / "references" / simples_base.NOME_BASE, Path(pasta) / simples_base.NOME_BASE)
            processo = subprocess.run(
                [sys.executable, "-I", str(Path(pasta) / "sugestao.py"), "--ncm", "10063021", "--data", "2027-03-15"],
                capture_output=True, text=True, cwd=pasta)
        self.assertEqual(processo.returncode, 0, processo.stderr)
        self.assertEqual(json.loads(processo.stdout)["sugestoes"][0]["cClassTrib"], "200003")


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
