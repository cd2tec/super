"""Testes da entrega da skill cbs-ibs-simples (render.py e template.html)."""
import copy
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "core" / "cd2-super" / "skills" / "cbs-ibs-simples"
SHARED = ROOT / "core" / "cd2-super" / "shared" / "scripts"
ESPERADAS = ROOT / "conferencia" / "cbs-ibs-simples" / "esperadas.json"
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


render = load_module("simples_render", SKILL / "scripts" / "render.py")

EM, EN = chr(0x2014), chr(0x2013)
EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿]")
FORMATOS = ("html", "markdown", "whatsapp")
VERSAO = {"calculadora": "1.0.2", "base": "V0073", "conferida_em": "2026-11-22"}
AVISO_FIXO = ("Resultado gerencial. Esta skill foi feita pela CD2 Tech com o código e as tabelas da calculadora "
              "oficial e não é um aplicativo da Receita Federal. Confira com o seu contador.")
SELO = ("Conta igual à da calculadora oficial do Simples Nacional versão 1.0.2, base V0073, "
        "conferida em 22/11/2026.")
RECEITA_URL = "https://github.com/ReceitaFederal/calculadora-rtc-simples-nacional"
_CASOS = {c["id"]: c for c in json.loads(ESPERADAS.read_text(encoding="utf-8"))["casos"]}

C_OK = "tabela32-combinacoes-que-levam-a-um-regime-1-sem-sn-pleno"
C_MISTO = "item-com-erro-ao-lado-de-itens-calculados"
C_422 = "tabela32-erros-1-2-vsn-018"
C_NAO_CALC = "tabela32-combinacoes-que-levam-a-um-regime-1-1-somente-receita"
C_INCONS = "somente-receita-nfse-sem-campos-do-iss-e-c-class-trib-obrigatorio-sem-cclasstrib"
C_NAO_IMPL = "item-nao-implementado-listado-na-resolucao"
C_500 = "divergencia-data-da-operacao-ano-negativo-de-cinco-digitos-falha-da-oficial"
C_400 = "deve-retornar400-para-tp-doc-mal-formado"


def entrega(caso, **extra):
    c = _CASOS[caso]
    data = {
        "operacao": copy.deepcopy(c["requisicao"]["corpo"]),
        "calculo": {"status": c["resposta"]["status"], "corpo": copy.deepcopy(c["resposta"]["corpo"]),
                    "versao": dict(VERSAO)},
        "sugestoes": None, "exemplo": False, "aviso_versao": None,
    }
    data.update(extra)
    return data


def vsn015_de_2026():
    corpo = {"type": "about:blank", "title": "Erros no processamento do cálculo do Simples Nacional",
             "status": 422, "detail": "O cálculo contém 1 erro(s).",
             "errors": [{"type": "x", "title": "Tipo de documento fiscal (tpDoc) não suportado",
                         "code": "VSN-015", "pointer": "#/tpDoc",
                         "detail": "Tipo de documento não suportado pelo cálculo do Simples Nacional, recebido: '65'."}]}
    return {"operacao": {"id": "op-1", "data": "2026-12-31", "tpDoc": "65", "itens": []},
            "calculo": {"status": 422, "corpo": corpo, "versao": dict(VERSAO)},
            "sugestoes": None, "exemplo": False, "aviso_versao": None}


def sugestao_do_item(numero="1"):
    return {numero: {"cClassTrib": "200003", "cst": "200", "descricao": "Cesta básica nacional",
                     "motivo": "NCM na lista do anexo"}}


class ConteudoTest(unittest.TestCase):
    def test_total_em_destaque_e_o_do_json(self):
        e = entrega(C_OK)
        total = e["calculo"]["corpo"]["total"]
        self.assertEqual((total["vCBSSNtot"], total["vIBSSNtot"]), ("11.61", "0.13"))
        pagina = render.render(e, "html")
        self.assertRegex(pagina, r'class="stat[^"]*"[^>]*>R\$ 11,61<')
        self.assertRegex(pagina, r'class="stat[^"]*"[^>]*>R\$ 0,13<')
        self.assertLess(pagina.index("R$ 11,61"), pagina.index("<table"))
        for formato in ("markdown", "whatsapp"):
            texto = render.render(e, formato)
            self.assertIn("R$ 11,61", texto)
            self.assertIn("R$ 0,13", texto)

    def test_item_com_erro_traz_motivo_e_codigo(self):
        e = entrega(C_INCONS)
        for formato in FORMATOS:
            with self.subTest(formato):
                texto = render.render(e, formato)
                self.assertIn("VSN-020", texto)
                self.assertIn("Dado de entrada com problema", texto)
        pagina = render.render(e, "html")
        motivo = render.simples_motor.CODIGOS["VSN-020"]["descricao"]
        self.assertIn(motivo, pagina)
        misto = render.render(entrega(C_MISTO), "html")
        self.assertIn("Calculado", misto)
        self.assertIn("Dado de entrada com problema", misto)

    def test_item_nao_calculado_traz_motivo_oficial_e_codigo(self):
        pagina = render.render(entrega(C_NAO_CALC), "html")
        self.assertIn("Não calculado", pagina)
        self.assertIn("REGIME_SOMENTE_RECEITA", pagina)
        self.assertIn("apura o IBS e a CBS pelo regime regular", pagina)
        impl = render.render(entrega(C_NAO_IMPL), "html")
        self.assertIn("Ainda não tratado pela calculadora oficial", impl)
        self.assertIn("VSN-027", impl)

    def test_operacao_422_abre_pelo_erro_e_nao_por_total_zero(self):
        e = entrega(C_422)
        pagina = render.render(e, "html")
        self.assertIn("VSN-018", pagina)
        self.assertIn("tpRegimeIBSCBS incompatível com o CRT", pagina)
        self.assertIn("CRT=1 não admite tpRegimeIBSCBS=2", pagina)
        self.assertNotIn("R$ 0,00", pagina)
        self.assertNotIn("<table", pagina)
        for formato in ("markdown", "whatsapp"):
            texto = render.render(e, formato)
            self.assertIn("VSN-018", texto)
            self.assertNotIn("R$ 0,00", texto)

    def test_status_400_e_500_mostram_titulo_e_detalhe(self):
        for caso, trecho in ((C_400, "Tipo de documento fiscal inválido"), (C_500, "Falha técnica")):
            with self.subTest(caso):
                for formato in FORMATOS:
                    texto = render.render(entrega(caso), formato)
                    self.assertIn(trecho, texto)

    def test_data_de_2026_explica_que_comeca_em_2027(self):
        frase = ("As regras do Simples Nacional para a CBS e o IBS começam em 01/01/2027. "
                 "Refaça a conta com uma data a partir daí.")
        for formato in FORMATOS:
            with self.subTest(formato):
                texto = render.render(vsn015_de_2026(), formato)
                self.assertIn(frase, texto)
                self.assertIn("VSN-015", texto)
        # a mesma falha com data de 2027 em diante não ganha a explicação da data
        e = vsn015_de_2026()
        e["operacao"]["data"] = "2027-03-15"
        self.assertNotIn("começam em 01/01/2027", render.render(e, "html"))
        # data em DD/MM/AAAA também vale
        e["operacao"]["data"] = "31/12/2026"
        self.assertIn("começam em 01/01/2027", render.render(e, "html"))

    def test_selo_traz_versao_base_e_data_do_meta(self):
        for formato in FORMATOS:
            with self.subTest(formato):
                self.assertIn(SELO, render.render(entrega(C_OK), formato))
        e = entrega(C_OK)
        e["calculo"]["versao"] = {"calculadora": "9.9.9", "base": "V0099", "conferida_em": "2030-01-05"}
        self.assertIn("versão 9.9.9, base V0099, conferida em 05/01/2030.", render.render(e, "markdown"))

    def test_selo_sem_data_omite_a_clausula(self):
        e = entrega(C_OK)
        e["calculo"]["versao"]["conferida_em"] = ""
        for formato in FORMATOS:
            with self.subTest(formato):
                texto = render.render(e, formato)
                self.assertIn("versão 1.0.2, base V0073.", texto)
                self.assertNotIn("conferida em", texto)

    def test_sugestao_marca_o_item_e_o_selo(self):
        aviso = "Classificação sugerida pela CD2, confirme com o contador."
        frase_selo = "O selo vale para a conta, não para a classificação sugerida."
        e = entrega(C_OK, sugestoes=sugestao_do_item())
        for formato in FORMATOS:
            with self.subTest(formato):
                texto = render.render(e, formato)
                self.assertIn(aviso, texto)
                self.assertIn(frase_selo, texto)
        self.assertIn("200003", render.render(e, "html"))
        sem = render.render(entrega(C_OK), "html")
        self.assertNotIn(aviso, sem)
        self.assertNotIn(frase_selo, sem)

    def test_exemplo_poe_a_faixa(self):
        faixa = "Exemplo ilustrativo com alíquotas da documentação oficial. Não é a conta da sua empresa."
        for formato in FORMATOS:
            with self.subTest(formato):
                self.assertIn(faixa, render.render(entrega(C_OK, exemplo=True), formato))
                self.assertNotIn(faixa, render.render(entrega(C_OK), formato))
        pagina = render.render(entrega(C_OK, exemplo=True), "html")
        self.assertLess(pagina.index(faixa), pagina.index("R$ 11,61"))

    def test_avisos_fixos_nos_tres_formatos(self):
        for caso in (C_OK, C_422):
            for formato in FORMATOS:
                with self.subTest(caso=caso, formato=formato):
                    self.assertIn(AVISO_FIXO, render.render(entrega(caso), formato))

    def test_aviso_de_versao_nova(self):
        linha = "A Receita já publicou a versão 1.0.3. Atualize o plugin para a conta mais recente."
        novo = {"consultado": True, "ha_versao_nova": True, "calculadora": "1.0.3", "mensagem": "x"}
        for formato in FORMATOS:
            with self.subTest(formato):
                self.assertIn(linha, render.render(entrega(C_OK, aviso_versao=novo), formato))
        for aviso in (None, {"consultado": False, "ha_versao_nova": None, "calculadora": None, "mensagem": ""},
                      {"consultado": True, "ha_versao_nova": False, "calculadora": "1.0.2", "mensagem": ""}):
            with self.subTest(aviso=aviso):
                self.assertNotIn("já publicou a versão", render.render(entrega(C_OK, aviso_versao=aviso), "html"))

    def test_avisos_da_operacao_e_normalizacoes(self):
        e = entrega(C_OK)
        e["calculo"]["corpo"]["resolucaoAliquotas"]["avisos"] = ["Regime diverge do indicador de sublimite"]
        e["calculo"]["normalizacoes"] = ['Em itens[0].baseCalculo, o número 1000 virou o texto "1000.00".']
        for formato in ("html", "markdown"):  # o resumo de WhatsApp é curto e não os lista
            with self.subTest(formato):
                texto = render.render(e, formato)
                self.assertIn("Regime diverge do indicador de sublimite", texto)
                self.assertIn("virou o texto", texto)

    def test_memoria_de_calculo_fica_recolhida(self):
        pagina = render.render(entrega(C_OK), "html")
        self.assertRegex(pagina, r"<details class=\"memoria\">")
        self.assertNotIn("<details open", pagina)
        self.assertIn("Receita bruta do Simples Nacional de R$ 1.000,00", pagina)
        self.assertNotIn("<script", pagina)

    def test_operacao_sem_item_calculado_nao_quebra(self):
        for caso in (C_NAO_CALC, C_INCONS, C_NAO_IMPL, C_422, C_400, C_500):
            for formato in FORMATOS:
                with self.subTest(caso=caso, formato=formato):
                    self.assertTrue(render.render(entrega(caso), formato).strip())
        vazio = {"operacao": None, "calculo": {"status": 200, "corpo": {"objetos": [], "total": {}}, "versao": {}},
                 "sugestoes": None, "exemplo": False, "aviso_versao": None}
        for formato in FORMATOS:
            with self.subTest(vazio=formato):
                self.assertIn("Nenhum item foi calculado", render.render(vazio, formato))
        nao_calc = render.render(entrega(C_NAO_CALC), "html")
        self.assertIn("Nenhum item foi calculado", nao_calc)

    def test_entrada_malformada_nao_quebra(self):
        e = entrega(C_OK)
        e["calculo"]["corpo"]["objetos"].append({"nObj": "x", "estadoItem": 7, "tribCalc": "texto"})
        e["calculo"]["corpo"]["total"]["vCBSSNtot"] = "abc"
        e["operacao"] = {"itens": "nao e lista"}
        for formato in FORMATOS:
            with self.subTest(formato):
                self.assertTrue(render.render(e, formato))


class SegurancaETextoTest(unittest.TestCase):
    def _perigosa(self):
        e = entrega(C_MISTO)
        e["operacao"]["id"] = "<script>alert(1)</script>"
        for item in e["operacao"]["itens"]:
            item["descricao"] = '<img src=x onerror=alert(2)> "aspas"'
        return e

    def test_html_escapa_texto_da_pessoa(self):
        e = self._perigosa()
        e["sugestoes"] = {"1": {"cClassTrib": "<b>1</b>", "cst": "1", "descricao": "<script>x</script>", "motivo": "m"}}
        pagina = render.render(e, "html")
        self.assertNotIn("<script", pagina)
        self.assertNotIn("<img src=x", pagina)
        self.assertNotIn("<b>1</b>", pagina)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", pagina)
        self.assertIn("&lt;img src=x onerror=alert(2)&gt;", pagina)

    def test_texto_de_erro_oficial_tambem_e_escapado(self):
        e = entrega(C_422)
        e["calculo"]["corpo"]["errors"][0]["detail"] = "<script>boom()</script>"
        self.assertNotIn("<script", render.render(e, "html"))

    def test_formatos_de_texto_tiram_controle_e_nao_levam_html(self):
        e = entrega(C_MISTO)
        e["operacao"]["id"] = "op\x00\x07\x1b[31m-1\nlinha2"
        e["operacao"]["itens"][0]["descricao"] = "<b>x</b>\x08"
        for formato in ("markdown", "whatsapp"):
            with self.subTest(formato):
                texto = render.render(e, formato)
                self.assertFalse([c for c in texto if ord(c) < 32 and c not in "\n"], formato)
                self.assertNotIn("<b>", texto)
                self.assertNotIn("<script", texto)
        self.assertNotIn("\x00", render.render(e, "html"))

    def test_sem_emoji_e_sem_travessao(self):
        casos = [entrega(c) for c in (C_OK, C_MISTO, C_422, C_NAO_CALC, C_INCONS, C_NAO_IMPL, C_500, C_400)]
        casos.append(entrega(C_OK, exemplo=True, sugestoes=sugestao_do_item(),
                             aviso_versao={"consultado": True, "ha_versao_nova": True, "calculadora": "1.0.3"}))
        casos.append(vsn015_de_2026())
        for i, e in enumerate(casos):
            for formato in FORMATOS:
                with self.subTest(caso=i, formato=formato):
                    texto = render.render(e, formato)
                    self.assertNotIn(EM, texto)
                    self.assertNotIn(EN, texto)
                    self.assertIsNone(EMOJI.search(texto))

    def test_travessao_vindo_de_fora_vira_hifen(self):
        e = entrega(C_422)
        e["calculo"]["corpo"]["errors"][0]["detail"] = "antes " + EM + " depois " + EN + " fim"
        pagina = render.render(e, "html")
        self.assertNotIn(EM, pagina)
        self.assertNotIn(EN, pagina)

    def test_fonte_sem_travessao_literal(self):
        for arquivo in (SKILL / "scripts" / "render.py", SKILL / "assets" / "template.html", Path(__file__)):
            with self.subTest(arquivo.name):
                texto = arquivo.read_text(encoding="utf-8")
                self.assertNotIn(EM, texto)
                self.assertNotIn(EN, texto)


class LinksEFormatosTest(unittest.TestCase):
    def test_links_cd2_tem_utm_e_link_da_receita_nao(self):
        utm = "utm_source={{UTM_SOURCE}}&utm_medium=skill&utm_campaign=cbs-ibs-simples"
        for formato in ("html", "markdown"):
            with self.subTest(formato):
                texto = render.render(entrega(C_OK), formato)
                if formato == "html":
                    texto = texto.replace("&amp;", "&")
                self.assertEqual(texto.count(RECEITA_URL), 1)
                self.assertNotIn(RECEITA_URL + "?", texto)
                self.assertNotIn(RECEITA_URL + "/", texto)
                self.assertIn("https://cd2.com.br/?" + utm, texto)
                self.assertIn("https://wa.me/5519982355172?text=", texto)
                for url in re.findall(r"https?://[^\s\"')<]+", texto):
                    if "cd2.com.br" in url:
                        self.assertIn(utm, url)
                self.assertNotIn("calc-", texto)  # a skill não tem site de calculadora
                self.assertEqual(re.findall(r"\{\{(?!UTM_SOURCE\}\})[^}]*\}\}", texto), [])

    def test_nenhum_numero_da_pessoa_entra_em_url(self):
        e = entrega(C_OK)
        e["operacao"]["id"] = "PEDIDO-987654"
        for formato in ("html", "markdown"):
            for url in re.findall(r"https?://[^\s\"')<]+", render.render(e, formato)):
                self.assertNotIn("987654", url)

    def test_whatsapp_cabe_em_1000_caracteres(self):
        grande = entrega(C_MISTO, exemplo=True, sugestoes=sugestao_do_item(),
                         aviso_versao={"consultado": True, "ha_versao_nova": True, "calculadora": "1.0.3"})
        grande["operacao"]["id"] = "x" * 400
        grande["calculo"]["normalizacoes"] = ["troquei isto por aquilo " * 10] * 30
        erros = entrega(C_422)
        erros["calculo"]["corpo"]["errors"] = erros["calculo"]["corpo"]["errors"] * 12
        for nome, e in (("ok", entrega(C_OK)), ("misto", entrega(C_MISTO)), ("grande", grande), ("erros", erros),
                        ("2026", vsn015_de_2026()), ("500", entrega(C_500))):
            with self.subTest(nome):
                self.assertLessEqual(len(render.render(e, "whatsapp")), 1000)

    def test_whatsapp_traz_o_essencial_mesmo_apertado(self):
        grande = entrega(C_OK, exemplo=True, sugestoes=sugestao_do_item())
        grande["operacao"]["id"] = "y" * 400
        texto = render.render(grande, "whatsapp")
        self.assertIn("R$ 11,61", texto)
        self.assertIn(AVISO_FIXO, texto)
        self.assertIn("CD2 Tech", texto)

    def test_valores_em_formato_brasileiro(self):
        e = entrega(C_OK)
        e["calculo"]["corpo"]["total"]["vCBSSNtot"] = "1234567.80"
        e["calculo"]["corpo"]["objetos"][0]["tribCalc"]["vCBSSN"] = "1234.56"
        copia = json.dumps(e, sort_keys=True)
        pagina = render.render(e, "html")
        self.assertIn("R$ 1.234.567,80", pagina)
        self.assertIn("R$ 1.234,56", pagina)
        self.assertIn("1,1612%", pagina)
        self.assertNotIn("1234.56", pagina)
        self.assertNotIn("1234567.80", pagina)
        self.assertEqual(json.dumps(e, sort_keys=True), copia)  # a entrada não é alterada
        self.assertEqual(e["calculo"]["corpo"]["objetos"][0]["tribCalc"]["vCBSSN"], "1234.56")

    def test_aliquota_longa_e_mostrada_com_quatro_casas(self):
        e = entrega(C_OK)
        e["calculo"]["corpo"]["objetos"][0]["tribCalc"]["pCBSSN"] = "752.5006128478058164621202384920910041"
        self.assertIn("752,5006%", render.render(e, "html"))

    def test_pagina_funciona_sem_rede_e_sem_script(self):
        pagina = render.render(entrega(C_MISTO, sugestoes=sugestao_do_item()), "html")
        self.assertNotIn("<script", pagina)
        self.assertNotRegex(pagina, r"<link\b")
        self.assertNotRegex(pagina, r"@import")
        self.assertNotRegex(pagina, r'(?:src|href)="(?!https://(?:wa\.me|cd2\.com\.br|archeo\.cd2\.com\.br|github\.com)|data:|#)')
        self.assertNotRegex(pagina, r"url\((?!data:)")
        self.assertIn("data:font/woff2;base64,", pagina)
        self.assertIn("<svg", pagina)
        self.assertIn('class="cd2-assinatura"', pagina)
        self.assertIn("overflow-x", pagina)

    def test_formato_invalido(self):
        with self.assertRaises(ValueError):
            render.render(entrega(C_OK), "pdf")
        with self.assertRaises(ValueError):
            render.render({"operacao": {}}, "html")


class CliTest(unittest.TestCase):
    def _rodar(self, *args, entrada=None):
        return subprocess.run([sys.executable, str(SKILL / "scripts" / "render.py"), *args], input=entrada,
                              capture_output=True, text=True, encoding="utf-8")

    def test_cli_escreve_arquivo_e_stdout(self):
        with tempfile.TemporaryDirectory() as pasta:
            origem = Path(pasta) / "entrega.json"
            origem.write_text(json.dumps(entrega(C_OK)), encoding="utf-8")
            destino = Path(pasta) / "saida.md"
            feito = self._rodar("--input", str(origem), "--formato", "markdown", "--out", str(destino))
            self.assertEqual((feito.returncode, feito.stdout), (0, ""), feito.stderr)
            self.assertIn("R$ 11,61", destino.read_text(encoding="utf-8"))
            feito = self._rodar("--input", str(origem), "--formato", "whatsapp")
            self.assertEqual(feito.returncode, 0, feito.stderr)
            self.assertIn("R$ 11,61", feito.stdout)
            feito = self._rodar("--formato", "html", entrada=origem.read_text(encoding="utf-8"))
            self.assertEqual(feito.returncode, 0, feito.stderr)
            self.assertTrue(feito.stdout.startswith("<!doctype html>"))

    def test_cli_erro_e_json_limpo_sem_traceback(self):
        feito = self._rodar("--formato", "html", entrada="{nao e json")
        self.assertEqual(feito.returncode, 2)
        self.assertIn("errors", json.loads(feito.stdout))
        self.assertNotIn("Traceback", feito.stderr)
        feito = self._rodar("--formato", "html", entrada='{"operacao": {}}')
        self.assertEqual(feito.returncode, 2)
        self.assertIn("errors", json.loads(feito.stdout))
        feito = self._rodar("--input", "/nao/existe.json", "--formato", "html")
        self.assertEqual(feito.returncode, 2)
        self.assertNotIn("Traceback", feito.stderr)


if __name__ == "__main__":
    unittest.main()
