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


def _carregar_sugestao():
    # a pasta de scripts do regime regular também tem um sugestao.py: carrega este por caminho, com nome só dele
    if "sugestao_simples" not in sys.modules:
        spec = importlib.util.spec_from_file_location("sugestao_simples", SCRIPTS / "sugestao.py")
        modulo = importlib.util.module_from_spec(spec)
        sys.modules["sugestao_simples"] = modulo
        try:
            spec.loader.exec_module(modulo)
        except BaseException:
            sys.modules.pop("sugestao_simples", None)
            raise
    return sys.modules["sugestao_simples"]


sugestao = _carregar_sugestao()


def _carregar_calc():
    # os testes das outras calculadoras também têm um calc.py: carrega este com o mesmo nome de test_calc_simples
    if "calc_simples" not in sys.modules:
        spec = importlib.util.spec_from_file_location("calc_simples", SCRIPTS / "calc.py")
        modulo = importlib.util.module_from_spec(spec)
        sys.modules["calc_simples"] = modulo
        spec.loader.exec_module(modulo)
    return sys.modules["calc_simples"]


calc = _carregar_calc()
_spec = importlib.util.spec_from_file_location("versao_da_base", ROOT / "tests" / "versao_da_base.py")
versao_da_base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(versao_da_base)
ancorado_na = versao_da_base.ancorado_na

DATA = date(2027, 3, 15)


class Amostras:
    """NCMs tirados da própria base, para os testes valerem com as tabelas que o robô publicar.

    Os primeiros na ordem do código, sempre os mesmos para a mesma base."""

    def __init__(self, base):
        self.base = base
        self._cache = {}

    def sugerir(self, ncm, tp_doc=65):
        return sugestao.sugerir(ncm, tp_doc, DATA, self.base)

    def codigos(self, ncm):
        return [s["cClassTrib"] for s in self.sugerir(ncm)["sugestoes"]]

    def vigentes(self):
        if "vigentes" not in self._cache:
            self._cache["vigentes"] = [n for (n,) in self.base._con.execute(
                "SELECT NCM_CD FROM NCM WHERE length(NCM_CD) = 8 ORDER BY NCM_CD").fetchall()
                if self.base.existe_ncm(n, DATA)]
        return self._cache["vigentes"]

    def da_lista(self, codigo="200003"):
        """Primeiro NCM que cai só na lista `codigo` (a sugestão é a lista e a regra geral)."""
        chave = ("lista", codigo)
        if chave not in self._cache:
            self._cache[chave] = next(n for n in self.vigentes() if self.codigos(n) == [codigo, "000001"])
        return self._cache[chave]

    def inexistente(self, inicio=85171231):
        """Primeiro código de 8 dígitos, a partir de `inicio`, que não é NCM vigente na base."""
        n = inicio
        while self.base.existe_ncm(f"{n:08d}", DATA):
            n += 1
        return f"{n:08d}"

    def sete_digitos(self):
        """Um NCM vigente que começa por zero e tem lista, escrito sem o zero."""
        if "sete" not in self._cache:
            self._cache["sete"] = next(n for n in self.vigentes() if n.startswith("0") and len(self.codigos(n)) > 1)
        return self._cache["sete"]

    def sete_digitos_inexistente(self):
        """Sete dígitos que, com o zero na frente, não são NCM vigente."""
        n = 2030000
        while self.base.existe_ncm(f"0{n:07d}", DATA):
            n += 1
        return f"{n:07d}"

    def no_seletivo(self, quantos=3):
        """NCMs da lista do Imposto Seletivo sem outra lista do perfil (a sugestão é só a regra geral)."""
        if "seletivo" not in self._cache:
            linhas = self.base._con.execute(
                "SELECT DISTINCT AAVP_NCM_CD FROM ALIQUOTA_AD_VALOREM_PRODUTO WHERE length(AAVP_NCM_CD) = 8 "
                "ORDER BY AAVP_NCM_CD").fetchall()
            self._cache["seletivo"] = [n for (n,) in linhas if self.base.existe_ncm(n, DATA)
                                       and self.base.no_imposto_seletivo(n, DATA) and self.codigos(n) == ["000001"]]
        return self._cache["seletivo"][:quantos]

    def fora_do_seletivo(self):
        """NCMs vigentes fora do Imposto Seletivo: um com lista e um comum."""
        com_lista = next(n for n in self.vigentes() if len(self.codigos(n)) > 1
                         and not self.base.no_imposto_seletivo(n, DATA))
        comum = next(n for n in self.vigentes() if self.codigos(n) == ["000001"]
                     and not self.base.no_imposto_seletivo(n, DATA))
        return [com_lista, comum]

    def duas_listas(self, quantos=10):
        """NCMs que caem em duas ou mais listas do perfil."""
        if "duas" not in self._cache:
            listas = {c for c, p in sugestao.PERFIL["classificacoes"].items() if p["tipo"] == "lista"}
            achados = []
            for n in self.vigentes():
                codigos = self.codigos(n)
                if len([c for c in codigos if c in listas]) >= 2 and codigos[-1] == "000001":
                    achados.append(n)
                    if len(achados) == quantos:
                        break
            self._cache["duas"] = achados
        return self._cache["duas"]


class SugestaoTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = simples_base.Base()
        cls.amostra = Amostras(cls.base)

    @classmethod
    def tearDownClass(cls):
        cls.base.fechar()

    def sugerir(self, ncm, tp_doc=65, data=DATA):
        return sugestao.sugerir(ncm, tp_doc, data, self.base)

    def codigos(self, resultado):
        return [s["cClassTrib"] for s in resultado["sugestoes"]]

    def test_arroz_sugere_cesta_basica(self):
        # a amostra vem da base (na V0073, o 02011000); o nome do teste vem do 10063021 que ele usava
        ncm = self.amostra.da_lista("200003")
        r = self.sugerir(ncm)
        self.assertEqual(r["ncm"], ncm)
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
        ncm = self.amostra.da_lista("200003")
        com_pontos = f"{ncm[:4]}.{ncm[4:6]}.{ncm[6:]}"
        r = self.sugerir(com_pontos)
        self.assertEqual(r["ncm"], ncm)
        self.assertEqual(self.codigos(r)[0], "200003")
        self.assertTrue(any(com_pontos in a and ncm in a for a in r["avisos"]))

    def test_ncm_com_sete_digitos_avisa_do_zero(self):
        # a amostra vem da base (na V0073, o 02011000 escrito 2011000)
        ncm = self.amostra.sete_digitos()
        r = self.sugerir(ncm[1:])
        self.assertEqual(r["ncm"], ncm)
        self.assertTrue(any("zero à esquerda" in a and ncm in a for a in r["avisos"]))
        self.assertEqual(self.codigos(r), self.amostra.codigos(ncm))

    def test_sete_digitos_que_nao_existem_depois_do_zero(self):
        sete = self.amostra.sete_digitos_inexistente()
        r = self.sugerir(sete)
        self.assertIsNone(r["ncm"])
        self.assertEqual(r["sugestoes"], [])
        self.assertTrue(any("zero à esquerda" in a and "0" + sete in a for a in r["avisos"]))
        self.assertTrue(any("não encontrado" in a for a in r["avisos"]))

    def test_ncm_que_nao_existe(self):
        r = self.sugerir(self.amostra.inexistente())
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

    def test_dica_vem_como_conferir_e_com_a_regra_geral_por_ultimo(self):
        # na V0073 a amostra é um remédio (dica 200032); aqui vem do primeiro NCM que recebe uma dica do perfil
        dicas = {c for d in sugestao.PERFIL["dicas"] for c in d["codigos"]}
        ncm = next(n for n in self.amostra.vigentes() if dicas & set(self.amostra.codigos(n)))
        r = self.sugerir(ncm)
        codigos = self.codigos(r)
        self.assertEqual(codigos[-1], "000001")
        dica = next(s for s in r["sugestoes"] if s["cClassTrib"] in dicas)
        self.assertIn("confirme com o contador", dica["motivo"])

    @ancorado_na("V0073")
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
                    self.assertIn(f"No regime regular, redução de {s['reducao']}%", s["motivo"])
                    self.assertIn(sugestao.TEXTO_SEM_EFEITO, s["motivo"])
                aviso = next(a for a in r["avisos"] if "mais de uma lista" in a)
                self.assertIn("menor redução no regime regular", aviso)
                self.assertIn("No Simples, o valor desta conta não muda entre elas", aviso)
                self.assertNotIn("nunca fica abaixo do real", aviso)
                self.assertIn("natureza do seu produto", aviso)

    def test_duas_listas_na_base_da_skill_saem_da_menor_reducao_para_a_maior(self):
        # a mesma regra da evidência acima, com amostra da base que estiver na skill
        amostra = self.amostra.duas_listas()
        self.assertTrue(amostra)
        for ncm in amostra:
            with self.subTest(ncm=ncm):
                r = self.sugerir(ncm)
                self.assertEqual(self.codigos(r)[-1], "000001")
                reducoes = [int(s["reducao"]) for s in r["sugestoes"][:-1]]
                self.assertEqual(reducoes, sorted(reducoes))
                aviso = next(a for a in r["avisos"] if "mais de uma lista" in a)
                self.assertIn("menor redução no regime regular", aviso)
                self.assertIn("No Simples, o valor desta conta não muda entre elas", aviso)
                self.assertNotIn("nunca fica abaixo do real", aviso)

    def test_uma_lista_so_nao_avisa_de_varias_listas(self):
        self.assertFalse(any("mais de uma lista" in a for a in self.sugerir(self.amostra.da_lista())["avisos"]))

    def test_regra_geral_fica_sempre_por_ultimo_com_reducao_zero(self):
        amostra = [self.amostra.da_lista(), *self.amostra.duas_listas(1), *self.amostra.no_seletivo(1),
                   self.ncm_comum()]
        for ncm in amostra:
            ultima = self.sugerir(ncm)["sugestoes"][-1]
            self.assertEqual((ultima["cClassTrib"], ultima["reducao"]), ("000001", "0"))

    @ancorado_na("V0073")
    def test_remedio_lista_vem_antes_da_dica_e_a_dica_que_promete_mais_avisa(self):
        # como no mini app: a lista que o NCM cumpre (200030) fica na frente, a dica depois
        r = self.sugerir("30049099")
        self.assertEqual(self.codigos(r), ["200030", "200032", "200009", "000001"])
        self.assertTrue(any("200009" in a and "mais redução no regime regular" in a
                            and "não muda o valor desta conta" in a for a in r["avisos"]))
        self.assertFalse(any("promete" in a for a in r["avisos"]))

    @ancorado_na("V0073")
    def test_reducao_lida_da_base_bate_com_a_do_perfil_antigo_na_v0073(self):
        # Âncora de regressão: os valores que o perfil copiava do mini app quando as reduções eram texto fixo.
        # Só vale para a V0073. Em outra versão o teste é pulado e nunca barra uma atualização de tabelas.
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
        ncm = self.amostra.da_lista("200003")
        seletivo = self.amostra.no_seletivo(1)[0]
        reducao_antes = self.sugerir(ncm)["sugestoes"][0]["reducao"]
        cesta = self.base.classificacao_cbs_ibs("200003", DATA)["id"]
        with tempfile.TemporaryDirectory() as pasta:
            destino = Path(pasta) / "base-receita.db"
            shutil.copy(SCRIPTS.parent / "references" / simples_base.NOME_BASE, destino)
            con = sqlite3.connect(destino)
            con.execute("UPDATE PERCENTUAL_REDUCAO SET PERE_VALOR = 70 WHERE PERE_CLTR_ID = ? AND PERE_TBTO_ID = 2",
                        (cesta,))
            con.execute("UPDATE PERCENTUAL_REDUCAO SET PERE_VALOR = 40.5 WHERE PERE_CLTR_ID = ? AND PERE_TBTO_ID = 3",
                        (cesta,))
            con.execute("INSERT INTO VERSAO_BASE_DADO (VRBD_ID, VRBD_DATA, VRBD_DESCRICAO, VRBD_VERSAO_BASE_DADO) "
                        "VALUES (9998, '2027-01-02 00:00:00', 'teste do robô', 'V9998')")
            con.commit()
            con.close()
            with simples_base.Base(destino) as nova:
                r = sugestao.sugerir(ncm, 65, DATA, nova)
                self.assertEqual(r["sugestoes"][0]["cClassTrib"], "200003")
                self.assertEqual(r["sugestoes"][0]["reducao"], "70")
                self.assertEqual(r["sugestoes"][0]["reducoes"],
                                 {"cbs": "70", "ibsEstadual": "40.5", "ibsMunicipal": "100"})
                self.assertIn("70% na CBS", r["sugestoes"][0]["motivo"])
                self.assertIn("V9998", sugestao.sugerir(seletivo, 65, DATA, nova)["imposto_seletivo"]["texto"])
        # a base original continua como estava
        self.assertEqual(self.sugerir(ncm)["sugestoes"][0]["reducao"], reducao_antes)
        self.assertNotIn("reducoes", self.sugerir(ncm)["sugestoes"][0])

    def test_imposto_seletivo_na_lista(self):
        # a amostra vem da base (na V0073, 22021000, 22030000 e 22041010)
        amostra = self.amostra.no_seletivo(3)
        self.assertTrue(amostra)
        for ncm in amostra:
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

    @ancorado_na("V0073")
    def test_agua_fica_fora_do_imposto_seletivo(self):
        # 22011000 (água) não está na lista que o mini app copia da Receita, ao contrário do que a revisão supôs
        self.assertFalse(self.sugerir("22011000")["imposto_seletivo"]["na_lista"])

    def test_imposto_seletivo_fora_da_lista(self):
        for ncm in self.amostra.fora_do_seletivo():
            with self.subTest(ncm=ncm):
                seletivo = self.sugerir(ncm)["imposto_seletivo"]
                self.assertFalse(seletivo["na_lista"])
                self.assertIn("não verifica o Imposto Seletivo além", seletivo["texto"])

    @ancorado_na("V0073")
    def test_imposto_seletivo_vem_da_base_com_a_excecao_da_tabela(self):
        # 88021100 tem exceção cadastrada (88026000), que a base expressa em EXCECAO_AD_VALOREM_PRODUTO
        for ncm, esperado in (("88021100", True), ("88029999", True), ("22011000", False)):
            with self.subTest(ncm=ncm):
                self.assertEqual(self.base.no_imposto_seletivo(ncm, DATA), esperado)
        self.assertTrue(self.sugerir("22030000")["imposto_seletivo"]["na_lista"])

    def test_sem_ncm_valido_nao_ha_imposto_seletivo(self):
        self.assertNotIn("imposto_seletivo", self.sugerir(self.amostra.inexistente()))

    def test_leitura_explica_a_ordem(self):
        r = self.sugerir(self.amostra.da_lista())
        self.assertIn("primeira sugestão é a mais provável", r["leitura"])
        self.assertIn("000001", r["leitura"])
        self.assertNotIn("mais provável", self.sugerir(self.amostra.inexistente())["leitura"])

    def test_texto_do_item_comum_cita_o_perfil_de_varejo(self):
        motivo = self.sugerir(self.ncm_comum())["sugestoes"][0]["motivo"]
        self.assertIn("nenhuma lista do perfil de varejo da CD2", motivo)

    def test_documento_fora_do_escopo_ou_data_sem_calculo(self):
        for tp_doc, data in ((57, DATA), (65, date(2026, 11, 22)), (99, DATA)):
            with self.subTest(tp_doc=tp_doc, data=data):
                r = self.sugerir(self.amostra.da_lista(), tp_doc, data)
                self.assertEqual(r["sugestoes"], [])
                self.assertTrue(r["avisos"])

    def test_nfe_tambem_sugere(self):
        self.assertEqual(self.codigos(self.sugerir(self.amostra.da_lista("200003"), 55))[0], "200003")

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
        amostra = self.amostra
        for ncm in (amostra.da_lista(), amostra.inexistente(), "", *amostra.duas_listas(1), amostra.sete_digitos()[1:]):
            with self.subTest(ncm=ncm):
                self.assertEqual(self.sugerir(ncm)["origem"], "leitura-cd2")

    def test_formato_da_sugestao(self):
        for s in self.sugerir(self.amostra.da_lista())["sugestoes"]:
            self.assertEqual(set(s), {"cClassTrib", "cst", "descricao", "reducao", "motivo", "efeito_no_simples",
                                      "efeito_texto"})

    def _mesmo_tipo_da_geral(self, base=None):
        base = base or self.base
        geral = base.classificacao(sugestao.PERFIL["geral"], DATA)[0]["tpRBSN"]
        return [c for c in sugestao.PERFIL["classificacoes"]
                if base.classificacao(c, DATA) and base.classificacao(c, DATA)[0]["tpRBSN"] == geral]

    def test_classificacao_com_o_tipo_de_receita_da_geral_nao_muda_a_conta(self):
        # O efeito vem da base: na V0073 as treze do perfil têm o tpRBSN da 000001
        codigos = self._mesmo_tipo_da_geral()
        self.assertIn("000001", codigos)
        for codigo in codigos:
            with self.subTest(codigo=codigo):
                id_classificacao = self.base.classificacao_cbs_ibs(codigo, DATA)["id"]
                reducoes = self.base.percentuais_reducao(id_classificacao, DATA)
                efeito, texto = sugestao._efeito(self.base, codigo, reducoes, DATA)
                self.assertEqual(efeito, "nenhum")
                if all(v == 0 for v in reducoes.values()):
                    self.assertEqual(texto, sugestao.TEXTO_SEM_REDUCAO)
                else:
                    self.assertTrue(texto.startswith("No regime regular, redução de "), texto)
                    self.assertTrue(texto.endswith(sugestao.TEXTO_SEM_EFEITO), texto)
        # e de ponta a ponta, em cada sugestão que sai para uma amostra da base
        for ncm in (self.amostra.da_lista(), *self.amostra.duas_listas(3), self.ncm_comum()):
            r = self.sugerir(ncm)
            for s in r["sugestoes"]:
                if s["cClassTrib"] in codigos:
                    with self.subTest(ncm=ncm, codigo=s["cClassTrib"]):
                        self.assertEqual(s["efeito_no_simples"], "nenhum")
                        self.assertIn(s["efeito_texto"], s["motivo"])
                        self.assertIn("decide se a conta aceita o NCM", s["motivo"])
            if all(s["cClassTrib"] in codigos for s in r["sugestoes"]):
                self.assertIn(sugestao.LEITURA_SEM_EFEITO, r["leitura"])

    def test_classificacao_com_outro_tipo_de_receita_avisa_que_pode_mudar(self):
        ncm = self.amostra.da_lista("200003")
        with tempfile.TemporaryDirectory() as pasta:
            destino = Path(pasta) / "base-receita.db"
            shutil.copy(SCRIPTS.parent / "references" / simples_base.NOME_BASE, destino)
            with simples_base.Base(destino) as copia:
                geral = copia.classificacao("000001", DATA)[0]["tpRBSN"]
            con = sqlite3.connect(destino)
            con.execute("UPDATE CLASSIFICACAO_TRIBUTARIA SET CLTR_TPRBSN = ? WHERE CLTR_CD = '200003'", (geral + 1,))
            con.commit()
            con.close()
            with simples_base.Base(destino) as nova:
                r = sugestao.sugerir(ncm, 65, DATA, nova)
        primeira, geral_sug = r["sugestoes"][0], r["sugestoes"][-1]
        self.assertEqual(primeira["cClassTrib"], "200003")
        self.assertEqual(primeira["efeito_no_simples"], "muda o tipo de receita")
        self.assertEqual(primeira["efeito_texto"], sugestao.TEXTO_TIPO_RECEITA)
        self.assertIn(sugestao.TEXTO_TIPO_RECEITA, primeira["motivo"])
        self.assertNotIn(sugestao.TEXTO_SEM_EFEITO, primeira["motivo"])
        self.assertEqual(geral_sug["efeito_no_simples"], "nenhum")
        self.assertIn(sugestao.LEITURA_TIPO_RECEITA, r["leitura"])
        self.assertNotIn(sugestao.LEITURA_SEM_EFEITO, r["leitura"])
        # a base original continua dizendo "nenhum"
        self.assertEqual(self.sugerir(ncm)["sugestoes"][0]["efeito_no_simples"], "nenhum")

    def test_a_cbs_e_o_ibs_nao_mudam_entre_classificacoes_do_mesmo_tipo_de_receita(self):
        # o fato em que os textos se apoiam: no motor, a sugestão com o tpRBSN da 000001 dá a mesma conta
        codigos = set(self._mesmo_tipo_da_geral())
        ncm = self.amostra.da_lista("200003")
        sugeridos = [s["cClassTrib"] for s in self.sugerir(ncm)["sugestoes"] if s["cClassTrib"] in codigos]
        self.assertIn("200003", sugeridos)
        self.assertIn("000001", sugeridos)

        def conta(codigo):
            operacao = {"id": "op-1", "versao": "3.0.0", "data": DATA.isoformat(), "tpDoc": "65", "CRT": "1",
                        "aliquotasEfetivas": {"fatorR": False, "impedimentoSublimite": False,
                                              "anexoI": {"pCBSSN": "1.1612", "pIBSSN": "0.0129"}},
                        "itens": [{"numero": 1, "cAtivSN": "01", "cClassTrib": codigo, "ncm": ncm,
                                   "baseCalculo": "1234.50", "valorImpostoSeletivo": "0.00"}]}
            saida = calc.executar_operacao(operacao, self.base)
            self.assertEqual(saida["status"], 200, saida)
            item = saida["corpo"]["objetos"][0]
            self.assertEqual(item["estadoItem"], "CALCULADO", item)
            total = saida["corpo"]["total"]
            return (item["tribCalc"]["vCBSSN"], item["tribCalc"]["vIBSSN"], total["vCBSSNtot"], total["vIBSSNtot"])

        referencia = conta("000001")
        for codigo in sugeridos:
            with self.subTest(codigo=codigo):
                self.assertEqual(conta(codigo), referencia)


class LinhaDeComandoTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with simples_base.Base() as base:
            cls.ncm = Amostras(base).da_lista("200003")

    def test_roda_de_pasta_achatada_com_a_base_ao_lado(self):
        # o pacote do GPT achata a skill numa pasta só: script e base lado a lado
        with tempfile.TemporaryDirectory() as pasta:
            for nome in ("sugestao.py", "simples_base.py"):
                shutil.copy(SCRIPTS / nome, Path(pasta) / nome)
            shutil.copy(SCRIPTS.parent / "references" / simples_base.NOME_BASE, Path(pasta) / simples_base.NOME_BASE)
            processo = subprocess.run(
                [sys.executable, "-I", str(Path(pasta) / "sugestao.py"), "--ncm", self.ncm, "--data", "2027-03-15"],
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
