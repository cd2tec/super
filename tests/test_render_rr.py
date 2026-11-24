"""Testes da entrega da skill cbs-ibs-regime-regular (render.py e template.html)."""
import html as htmllib
from decimal import Decimal
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "core" / "cd2-super" / "skills" / "cbs-ibs-regime-regular"
SHARED = ROOT / "core" / "cd2-super" / "shared" / "scripts"
for _p in (SHARED, SKILL / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


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


rr_render = load_module("rr_render", SKILL / "scripts" / "render.py")

EM, EN = chr(0x2014), chr(0x2013)
EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿]")
FORMATOS = ("html", "texto", "whatsapp")
LINK = ("https://calc-imposto.cd2.com.br/?utm_source={{UTM_SOURCE}}"
        "&utm_medium=skill&utm_campaign=cbs-ibs-regime-regular")
FONTES = [
    "https://www.diariodepernambuco.com.br/economia/2026/11/11729363-ministro.html",
    "https://www.gov.br/fazenda/pt-br/central-de-conteudo/publicacoes/2024-04-24_Regulamentacao.pdf",
    "https://github.com/ReceitaFederal/calculadora-rtc-regime-regular/blob/main/docs/exemplos.md",
]
# Números digitados pela pessoa: nenhum pode aparecer dentro de um endereço.
NCM, MUNICIPIO, ID = "84713012", "3550308", "pedido-98765"


def calculo_exemplo(cenario="reforma-completa"):
    notebook = {"nObj": 1, "estadoItem": "CALCULO_SIMULADO", "tribCalc": {"IBSCBS": {
        "CST": "000", "cClassTrib": "000001",
        "gIBSCBS": {"vBC": "5000.00", "gIBSUF": {"pIBSUF": "8.85", "vIBSUF": "442.50"},
                    "gIBSMun": {"pIBSMun": "8.85", "vIBSMun": "442.50"}, "vIBS": "885.00",
                    "gCBS": {"pCBS": "8.80", "vCBS": "440.00"}},
        "memoriaCalculo": {"cbs": "Memória da CBS do notebook.", "ibsUF": "Memória do IBS estadual do notebook.",
                           "ibsMun": "Memória do IBS municipal do notebook."}}}}
    cerveja = {"nObj": 2, "estadoItem": "CALCULO_SIMULADO", "tribCalc": {
        "IS": {"CSTIS": "000", "cClassTribIS": "000001", "vBCIS": "100.00", "pIS": "10.00", "pISEspec": "0.00",
               "adRemIS": "0.00", "uTrib": "UN", "qTrib": "1", "vIS": "10.00",
               "memoriaCalculo": "Memória do Imposto Seletivo da cerveja."},
        "IBSCBS": {"CST": "000", "cClassTrib": "000001",
                   "gIBSCBS": {"vBC": "110.00", "gIBSUF": {"pIBSUF": "8.85", "vIBSUF": "9.74"},
                               "gIBSMun": {"pIBSMun": "8.85", "vIBSMun": "9.74"}, "vIBS": "19.48",
                               "gCBS": {"pCBS": "8.80", "vCBS": "9.68"}},
                   "memoriaCalculo": {"cbs": "Memória da CBS da cerveja.", "ibsUF": "Memória do IBS estadual da cerveja.",
                                      "ibsMun": "Memória do IBS municipal da cerveja."}}}}
    saida = {
        "status": 200,
        "corpo": {"objetos": [notebook, cerveja], "total": {"tribCalc": {
            "ISTot": {"vIS": "10.00"},
            "IBSCBSTot": {"vBCIBSCBS": "5110.00",
                          "gIBS": {"gIBSUF": {"vIBSUF": "452.24"}, "gIBSMun": {"vIBSMun": "452.24"}, "vIBS": "904.48"},
                          "gCBS": {"vCBS": "449.68"}}}}},
        "versao": {"calculadora": "1.7.2", "base": "V0073", "conferida_em": "2026-11-24",
                   "conferencia": {"casos": 1368, "iguais": 1368}},
        "simulado": True,
        "aviso_origem": "Esta skill não é um aplicativo da Receita Federal.",
        "aviso": "Resultado simulado: as alíquotas de 2027 em diante são cenário ou informadas, "
                 "não as definitivas. Confira com o contador.",
    }
    if cenario == "reforma-completa":
        saida["cenario"] = {"cenario": "reforma-completa", "nome": "Reforma completa", "cbs": "8.8",
                            "ibs_estadual": "8.85", "ibs_municipal": "8.85", "hipotese_cd2": True,
                            "nota": "A divisão do IBS entre estado e município é uma hipótese da CD2.",
                            "checked_at": "2026-11-22", "fontes": FONTES}
    elif cenario == "em-2027":
        saida["cenario"] = {"cenario": "em-2027", "nome": "Em 2027", "cbs": "11.3", "ibs_estadual": "0.05",
                            "ibs_municipal": "0.05", "checked_at": "2026-11-22", "fontes": FONTES}
    return saida


def entrega_exemplo(cenario="reforma-completa", **extra):
    entrega = {
        "operacao": {"id": ID, "documento": "NF-e", "uf": "SP", "municipio": int(MUNICIPIO), "data": "2027-01-01",
                     "itens": [{"numero": 1, "descricao": "Notebook", "ncm": NCM, "valor": 5000.0},
                               {"numero": 2, "descricao": "Cerveja", "ncm": "22030000", "valor": 100.0}]},
        "calculo": calculo_exemplo(cenario),
    }
    entrega.update(extra)
    return entrega


def urls(texto):
    return re.findall(r"https?://[^\s\"'<>)]+", htmllib.unescape(texto))


class RenderBase(unittest.TestCase):
    def todos(self, entrega=None, **kw):
        entrega = entrega or entrega_exemplo(**kw)
        return {f: rr_render.render(entrega, f) for f in FORMATOS}


class ConteudoTest(RenderBase):
    def test_total_de_cbs_ibs_e_is(self):
        for formato, saida in self.todos().items():
            for esperado in ("R$ 449,68", "R$ 904,48", "R$ 10,00"):
                self.assertIn(esperado, saida, formato)
            for rotulo in ("CBS", "IBS", "Imposto Seletivo"):
                self.assertIn(rotulo, saida, formato)

    def test_item_a_item_e_memoria(self):
        for formato in ("html", "texto"):
            saida = self.todos()[formato]
            for esperado in ("Notebook", "Cerveja", "R$ 5.000,00", "R$ 885,00", "8,85%", "8,80%",
                             "Memória da CBS do notebook.", "Memória do IBS estadual da cerveja.",
                             "Memória do Imposto Seletivo da cerveja.", "Memória de cálculo"):
                self.assertIn(esperado, saida, formato)

    def test_selo_de_versao_em_todos_os_formatos(self):
        for formato, saida in self.todos().items():
            for esperado in ("1.7.2", "V0073", "24/11/2026", "1.368 de 1.368"):
                self.assertIn(esperado, saida, formato)

    def test_selo_sem_conferencia_nao_inventa_o_n_de_n(self):
        entrega = entrega_exemplo()
        del entrega["calculo"]["versao"]["conferencia"]
        saida = rr_render.render(entrega, "texto")
        self.assertIn("24/11/2026", saida)
        self.assertNotIn("1.368", saida)

    def test_conferencia_incompleta_diz_quantos_batem(self):
        entrega = entrega_exemplo()
        entrega["calculo"]["versao"]["conferencia"] = {"casos": 1368, "iguais": 1300}
        saida = rr_render.render(entrega, "texto")
        self.assertIn("1.300 de 1.368", saida)

    def test_aviso_de_simulado_em_2027(self):
        for formato, saida in self.todos().items():
            self.assertRegex(saida.lower(), r"simulad", formato)
            self.assertIn("não as definitivas", saida, formato)

    def test_simulado_sai_mesmo_sem_a_chave_quando_a_data_e_2027_ou_depois(self):
        entrega = entrega_exemplo()
        entrega["calculo"].pop("simulado")
        entrega["calculo"].pop("aviso")
        for objeto in entrega["calculo"]["corpo"]["objetos"]:
            objeto["estadoItem"] = "CALCULADO"
        for formato in FORMATOS:
            self.assertIn("simulado", rr_render.render(entrega, formato).lower(), formato)

    def test_sem_aviso_de_simulado_antes_de_2027(self):
        entrega = entrega_exemplo(cenario=None)
        entrega["operacao"]["data"] = "2026-03-10"
        entrega["calculo"]["simulado"] = False
        for objeto in entrega["calculo"]["corpo"]["objetos"]:
            objeto["estadoItem"] = "CALCULADO"
        entrega["calculo"].pop("aviso")
        for formato in FORMATOS:
            self.assertNotIn("simulad", rr_render.render(entrega, formato).lower(), formato)

    def test_cenario_com_data_e_fonte(self):
        for formato, saida in self.todos().items():
            for esperado in ("Reforma completa", "22/11/2026", "8,8%", "8,85%"):
                self.assertIn(esperado, saida, formato)
        for formato in ("html", "texto"):
            saida = self.todos()[formato]
            for fonte in FONTES:
                self.assertIn(fonte, htmllib.unescape(saida), formato)
        wa = self.todos()["whatsapp"]
        for dominio in ("diariodepernambuco.com.br", "gov.br", "github.com"):
            self.assertIn(dominio, wa)

    def test_hipotese_da_cd2_na_reforma_completa(self):
        for formato, saida in self.todos().items():
            self.assertRegex(saida, r"divisão do IBS entre estado e município é (uma )?hipótese da CD2", formato)

    def test_sem_hipotese_no_cenario_em_2027(self):
        for formato, saida in self.todos(cenario="em-2027").items():
            self.assertIn("Em 2027", saida, formato)
            self.assertNotIn("hipótese da CD2", saida, formato)

    def test_aliquotas_informadas_sem_cenario(self):
        for formato, saida in self.todos(cenario=None).items():
            self.assertIn("informadas", saida, formato)
            self.assertNotIn("hipótese da CD2", saida, formato)

    def test_nao_e_aplicativo_da_receita(self):
        for formato, saida in self.todos().items():
            self.assertIn("não é aplicativo da Receita Federal", saida, formato)

    def test_nunca_a_frase_do_simples_sobre_reducao_sem_efeito(self):
        entrega = entrega_exemplo(sugestoes={"1": {"cClassTrib": "200003", "cst": "200",
                                                   "descricao": "Cesta", "reducao": "60"}})
        for formato in FORMATOS:
            saida = rr_render.render(entrega, formato)
            self.assertNotIn("sem efeito", saida, formato)
            self.assertNotIn("Simples", saida, formato)
        self.assertIn("redução de 60%", rr_render.render(entrega, "texto"))

    def test_exemplo_marcado_em_toda_a_entrega(self):
        for formato, saida in self.todos(entrega_exemplo(exemplo=True)).items():
            self.assertIn("Exemplo ilustrativo", saida, formato)

    def test_observacoes_saem_logo_abaixo_dos_totais(self):
        entrega = entrega_exemplo(observacoes=["O total não inclui o frete."])
        for formato in FORMATOS:
            self.assertIn("O total não inclui o frete.", rr_render.render(entrega, formato), formato)

    def test_item_com_erro_mostra_o_motivo_e_nao_entra_no_total(self):
        entrega = entrega_exemplo()
        entrega["calculo"]["corpo"]["objetos"][1] = {
            "nObj": 2, "estadoItem": "INCONSISTENCIA_ENTRADA",
            "erros": [{"title": "Imposto Seletivo não informado", "code": "REG-013", "detail": "Falta o bloco do IS."}]}
        for formato in ("html", "texto"):
            saida = rr_render.render(entrega, formato)
            self.assertIn("REG-013", saida)
            self.assertIn("Falta o bloco do IS.", saida)
        self.assertIn("REG-013", rr_render.render(entrega, "whatsapp"))

    def test_erro_da_operacao(self):
        entrega = {"operacao": {}, "calculo": {"status": 422, "corpo": {
            "title": "Erros no processamento do cálculo", "detail": "O cálculo contém 1 erro(s).",
            "errors": [{"title": "Data inválida", "code": "REG-001", "detail": "Informe a data."}]},
            "versao": {"calculadora": "1.7.2", "base": "V0073", "conferida_em": "2026-11-24"}}}
        for formato, saida in self.todos(entrega).items():
            self.assertIn("REG-001", saida, formato)
            self.assertIn("não é aplicativo da Receita Federal", saida, formato)
            self.assertNotIn("Memória de cálculo", saida, formato)

    def test_entrega_invalida_levanta(self):
        with self.assertRaises(ValueError):
            rr_render.render({"calculo": 1}, "html")
        with self.assertRaises(ValueError):
            rr_render.render(entrega_exemplo(), "pdf")


class ConviteELinksTest(RenderBase):
    def test_convite_com_o_link_exato_da_calc_imposto(self):
        for formato, saida in self.todos().items():
            self.assertIn(LINK, htmllib.unescape(saida), formato)
            self.assertIn("calc-imposto", saida, formato)

    def test_nenhum_numero_digitado_vai_para_endereco(self):
        entrega = entrega_exemplo(observacoes=["Pedido " + ID])
        for formato in FORMATOS:
            for endereco in urls(rr_render.render(entrega, formato)):
                for numero in (NCM, MUNICIPIO, ID, "5000", "22030000", "449", "904"):
                    self.assertNotIn(numero, endereco, (formato, endereco))

    def test_so_enderecos_conhecidos(self):
        permitidos = ("https://calc-imposto.cd2.com.br/", "https://cd2.com.br/", "https://wa.me/",
                      "https://archeo.cd2.com.br/", "https://www.diariodepernambuco", "https://www.gov.br/",
                      "https://github.com/ReceitaFederal/", "http://www.w3.org/2000/svg", "http://www.w3.org/1999/xlink")
        for formato, saida in self.todos().items():
            for endereco in urls(saida):
                self.assertTrue(endereco.startswith(permitidos), (formato, endereco))


class FormatoTest(RenderBase):
    def test_whatsapp_cabe_em_mil_caracteres_com_o_essencial(self):
        saida = rr_render.render(entrega_exemplo(), "whatsapp")
        self.assertLessEqual(len(saida), 1000)
        entrega = entrega_exemplo(observacoes=["Observação muito longa. " * 20] * 5)
        entrega["operacao"]["itens"][0]["descricao"] = "X" * 500
        saida = rr_render.render(entrega, "whatsapp")
        self.assertLessEqual(len(saida), 1000)
        self.assertIn("simulad", saida.lower())
        for essencial in ("1.7.2", "Reforma completa", "hipótese da CD2", LINK,
                          "não é aplicativo da Receita Federal"):
            self.assertIn(essencial, saida)

    def test_sem_travessao_e_sem_emoji(self):
        entrega = entrega_exemplo(observacoes=["Fala" + EM + "teste" + EN])
        entrega["operacao"]["itens"][0]["descricao"] = "Item" + EM + "x"
        for formato in FORMATOS:
            saida = rr_render.render(entrega, formato)
            self.assertNotIn(EM, saida, formato)
            self.assertNotIn(EN, saida, formato)
            self.assertIsNone(EMOJI.search(saida), formato)

    def test_html_escapa_texto_de_fora(self):
        entrega = entrega_exemplo(observacoes=["<script>alert(1)</script>"])
        entrega["operacao"]["itens"][0]["descricao"] = '"><img src=x onerror=1>'
        saida = rr_render.render(entrega, "html")
        self.assertNotIn("<script>alert", saida)
        self.assertNotIn("<img src=x", saida)

    def test_texto_nao_deixa_html_nem_link_de_fora(self):
        entrega = entrega_exemplo(observacoes=["[clique](http://evil.example) <b>oi</b>"])
        saida = rr_render.render(entrega, "texto")
        self.assertNotIn("](http://evil", saida)
        self.assertNotIn("<b>", saida)

    def test_html_pagina_responsiva_com_pilula_sem_quebra(self):
        saida = rr_render.render(entrega_exemplo(), "html")
        self.assertTrue(saida.startswith("<!doctype html>"))
        self.assertIn('name="viewport"', saida)
        self.assertIn("white-space: nowrap", re.search(r"\.pill \{[^}]*\}", saida).group(0))
        self.assertIn("@media (max-width: 860px)", saida)
        self.assertIn("prefers-color-scheme: dark", saida)
        self.assertIn("@font-face", saida)
        self.assertNotIn("{{TITLE}}", saida)
        self.assertEqual(re.findall(r"\{\{(?!UTM_SOURCE)[A-Z_]+\}\}", saida), [])

    def test_html_tem_secoes_na_ordem(self):
        saida = rr_render.render(entrega_exemplo(), "html")
        ordem = ["TOTAL DA OPERAÇÃO", "Itens da operação", "Memória de cálculo", "CONTA CONFERIDA"]
        posicoes = [saida.index(t) for t in ordem]
        self.assertEqual(posicoes, sorted(posicoes))

    def test_assets_no_lugar(self):
        self.assertTrue((SKILL / "assets" / "template.html").is_file())
        self.assertTrue((SKILL / "assets" / "fonts" / "Manrope-Variable.woff2").is_file())
        self.assertTrue((SKILL / "assets" / "fonts" / "OFL.txt").is_file())
        simples = ROOT / "core" / "cd2-super" / "skills" / "cbs-ibs-simples" / "assets" / "fonts"
        for nome in ("Manrope-Variable.woff2", "OFL.txt"):
            self.assertEqual((SKILL / "assets" / "fonts" / nome).read_bytes(), (simples / nome).read_bytes())

    def test_conferencia_lida_da_base_quando_a_entrega_nao_traz(self):
        entrega = entrega_exemplo()
        del entrega["calculo"]["versao"]["conferencia"]
        saida = rr_render.render(entrega, "texto", conferencia_da_base=True)
        self.assertRegex(saida, r"[\d.]+ de [\d.]+ casos")


class LinhaDeComandoTest(unittest.TestCase):
    def rodar(self, *args, entrada=None):
        return subprocess.run([sys.executable, str(SKILL / "scripts" / "render.py"), *args], input=entrada,
                              capture_output=True, text=True, encoding="utf-8")

    def test_arquivo_e_formato(self):
        with tempfile.TemporaryDirectory() as pasta:
            caminho = Path(pasta) / "entrega.json"
            caminho.write_text(json.dumps(entrega_exemplo()), encoding="utf-8")
            for formato in FORMATOS:
                r = self.rodar(str(caminho), "--formato", formato)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn("1.7.2", r.stdout)
                self.assertIn("calc-imposto.cd2.com.br", r.stdout)
            saida = Path(pasta) / "conta.html"
            r = self.rodar(str(caminho), "--formato", "html", "--out", str(saida))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(saida.read_text(encoding="utf-8").startswith("<!doctype html>"))

    def test_entrada_padrao(self):
        r = self.rodar("--formato", "texto", entrada=json.dumps(entrega_exemplo()))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Reforma completa", r.stdout)

    def test_json_invalido_sai_com_2_e_json_limpo(self):
        r = self.rodar("--formato", "texto", entrada="{nao")
        self.assertEqual(r.returncode, 2)
        self.assertIn("errors", json.loads(r.stdout))

    def test_arquivo_inexistente_sai_com_2(self):
        r = self.rodar("/nao/existe.json", "--formato", "html")
        self.assertEqual(r.returncode, 2)
        self.assertNotIn("Traceback", r.stderr)

    def test_formato_obrigatorio(self):
        self.assertNotEqual(self.rodar("x.json").returncode, 0)


class SaidaRealDoCalcTest(unittest.TestCase):
    """A entrega montada com a saída de verdade do calc.py (porta em português, cenário reforma-completa)."""

    def test_calc_real(self):
        calc = load_module("rr_calc_render_teste", SKILL / "scripts" / "calc.py")
        rr_base = sys.modules["rr_base"]
        operacao = {"id": "p1", "documento": "NF-e", "uf": "SP", "municipio": 3550308, "data": "2027-01-01",
                    "itens": [{"numero": 1, "descricao": "Notebook", "ncm": "84713012", "cst": "000",
                               "classificacao": "000001", "valor": Decimal("5000.00"), "quantidade": 1, "unidade": "UN"}]}
        with rr_base.Base() as base:
            saida = calc.executar_operacao(operacao, base, cenario="reforma-completa")
        saida = json.loads(json.dumps(saida, default=str))
        operacao = json.loads(json.dumps(operacao, default=str))
        for formato in FORMATOS:
            texto = rr_render.render({"operacao": operacao, "calculo": saida}, formato)
            self.assertIn("R$ 440,00", texto, formato)
            self.assertIn("R$ 885,00", texto, formato)
            self.assertIn("Reforma completa", texto, formato)
            self.assertIn("simulad", texto.lower(), formato)


if __name__ == "__main__":
    unittest.main()
