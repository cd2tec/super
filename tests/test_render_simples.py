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
        self.assertNotIn("overflow-x: hidden", pagina)  # nada de esconder estouro: o texto longo quebra
        self.assertIn("overflow-wrap", pagina)

    def test_formato_invalido(self):
        with self.assertRaises(ValueError):
            render.render(entrega(C_OK), "pdf")
        with self.assertRaises(ValueError):
            render.render({"operacao": {}}, "html")


HOSTIL = "<script>alert(1)</script><img src=x onerror=y>[x](javascript:alert(1)) `rm` java\u2028script:\u00a0\u2009x"
IS_TEXTO = "Este NCM está na leitura da CD2 da lista do Imposto Seletivo (Lei X), que começa em 2027. Informe o valor no documento."


def _tudo_hostil(valor, texto):
    if isinstance(valor, str):
        return texto
    if isinstance(valor, list):
        return [_tudo_hostil(v, texto) for v in valor]
    if isinstance(valor, dict):
        return {k: _tudo_hostil(v, texto) for k, v in valor.items()}
    return valor




class RobustezTest(unittest.TestCase):
    def _entregas(self):
        base = entrega(C_MISTO, sugestoes={"1": {**sugestao_do_item()["1"], "reducao": "100",
                                                 "imposto_seletivo": {"na_lista": True, "texto": IS_TEXTO}}},
                       aviso_versao={"consultado": True, "ha_versao_nova": True, "calculadora": "1.0.3", "mensagem": "m"})
        base["calculo"]["normalizacoes"] = ["a"]
        base["calculo"]["corpo"]["resolucaoAliquotas"]["avisos"] = ["a"]
        base["observacoes"] = ["a", "b"]
        yield "misto", base
        for caso in (C_422, C_NAO_CALC, C_INCONS, C_500, C_400, C_OK):
            yield caso, entrega(caso, sugestoes={"1": {"cClassTrib": "1", "cst": "1", "descricao": "d", "reducao": "0"}},
                                aviso_versao={"consultado": True, "ha_versao_nova": True, "calculadora": "1.0.3"},
                                observacoes=["o"])
        yield "2026", vsn015_de_2026()

    def test_campo_hostil_em_toda_a_entrega_nao_sobrevive(self):
        for nome, e in self._entregas():
            hostil = _tudo_hostil(e, HOSTIL)
            for formato in FORMATOS:
                with self.subTest(caso=nome, formato=formato):
                    texto = render.render(hostil, formato)
                    if formato == "html":
                        for tag in ("<script", "<img src=x"):
                            self.assertNotIn(tag, texto)
                        self.assertNotIn("\u2028", texto)
                    else:
                        for proibido in ("<", ">", "`", "javascript:"):
                            self.assertNotIn(proibido, texto)
                        # o único link possível é o nosso, da Receita, no Markdown
                        self.assertEqual(texto.count("]("), 1 if formato == "markdown" else 0)

    def test_versao_hostil_vira_desconhecida_em_todos_os_formatos(self):
        e = entrega(C_OK, aviso_versao={"consultado": True, "ha_versao_nova": True,
                                        "calculadora": 'javascript:alert(1)"><i>x'})
        e["calculo"]["versao"] = {"calculadora": '<b>1</b>](javascript:alert(1))', "base": 'V"><img onerror=x>',
                                  "conferida_em": "<i>2026-11-22"}
        for formato in FORMATOS:
            with self.subTest(formato):
                texto = render.render(e, formato)
                self.assertIn("versão desconhecida, base desconhecida.", texto)
                self.assertIn("A Receita já publicou a versão desconhecida.", texto)
                self.assertNotIn("conferida em", texto)
                self.assertNotIn("javascript:", texto)
                self.assertNotIn("<img", texto)
        ok = entrega(C_OK)
        ok["calculo"]["versao"] = {"calculadora": "v1.0.2", "base": "V0073", "conferida_em": "2026-11-22"}
        self.assertIn("versão v1.0.2, base V0073, conferida em 22/11/2026.", render.render(ok, "markdown"))
        ok["calculo"]["versao"]["calculadora"] = "1." * 20 + "1"
        self.assertIn("versão desconhecida", render.render(ok, "markdown"))

    def test_whatsapp_nunca_passa_de_mil_com_campos_gigantes(self):
        grande = "Z" * 2000
        cenarios = []
        for caso in (C_OK, C_MISTO, C_422, C_500, C_NAO_CALC):
            e = _tudo_hostil(entrega(caso, exemplo=True, sugestoes=sugestao_do_item(), observacoes=["o"] * 5,
                                     aviso_versao={"consultado": True, "ha_versao_nova": True, "calculadora": "1.0.3"}), grande)
            cenarios.append((caso + "-texto", e))
        vsn = vsn015_de_2026()
        vsn["exemplo"] = True
        vsn["calculo"]["versao"]["calculadora"] = "V" + "1." * 9 + "1"
        vsn["calculo"]["versao"]["base"] = "V" + "9" * 19
        vsn["calculo"]["corpo"]["errors"][0]["title"] = grande
        vsn["operacao"]["id"] = grande
        cenarios.append(("2026-exemplo", vsn))
        numeros = entrega(C_MISTO, exemplo=True, sugestoes=sugestao_do_item())
        numeros["calculo"]["versao"] = {"calculadora": "V" + "1." * 9 + "1", "base": "V" + "9" * 19, "conferida_em": "2026-11-22"}
        numeros["calculo"]["corpo"]["total"]["vCBSSNtot"] = "9" * 40 + ".99"
        numeros["calculo"]["corpo"]["total"]["vIBSSNtot"] = "8" * 40 + ".99"
        numeros["operacao"]["id"] = grande
        cenarios.append(("numeros", numeros))
        for nome, e in cenarios:
            with self.subTest(nome):
                texto = render.render(e, "whatsapp")
                self.assertLessEqual(len(texto), 1000)
                self.assertIn(AVISO_FIXO, texto)
                self.assertIn("CD2 Tech", texto)
                if "-texto" not in nome:
                    self.assertTrue("Conta igual" in texto)

    def test_whatsapp_com_o_pior_caso_mantem_total_selo_e_aviso(self):
        e = entrega(C_OK, exemplo=True, sugestoes=sugestao_do_item(),
                    aviso_versao={"consultado": True, "ha_versao_nova": True, "calculadora": "1.0.3"})
        e["calculo"]["versao"] = {"calculadora": "V" + "1." * 9 + "1", "base": "V" + "9" * 19, "conferida_em": "2026-11-22"}
        e["operacao"]["id"] = "o" * 2000
        texto = render.render(e, "whatsapp")
        self.assertLessEqual(len(texto), 1000)
        for trecho in ("R$ 11,61", "R$ 0,13", "Conta igual", AVISO_FIXO):
            self.assertIn(trecho, texto)

    def test_total_nao_numerico_com_itens_calculados_diz_indisponivel(self):
        e = entrega(C_OK)
        e["calculo"]["corpo"]["total"]["vCBSSNtot"] = "abc"
        for formato in FORMATOS:
            with self.subTest(formato):
                texto = render.render(e, formato)
                self.assertIn("Total indisponível", texto)
                self.assertNotIn("Nenhum item foi calculado", texto)

    def test_espacos_unicode_viram_espaco_e_separadores_somem(self):
        e = entrega(C_422)
        e["calculo"]["corpo"]["errors"][0]["detail"] = "a\u00a0b\u2009c\u3000d\u2028e\u2029f\u200bg\ufeffh"
        for formato in FORMATOS:
            with self.subTest(formato):
                texto = render.render(e, formato)
                self.assertIn("a b c de" + "fgh", texto)
                for c in "\u00a0\u2009\u3000\u2028\u2029\u200b\ufeff":
                    self.assertNotIn(c, texto)

    def test_codigo_nunca_e_cortado_no_whatsapp(self):
        e = entrega(C_422)
        e["calculo"]["corpo"]["errors"][0]["title"] = "T" * 500
        texto = render.render(e, "whatsapp")
        self.assertIn("(VSN-018)", texto)
        self.assertLessEqual(len(texto), 1000)


class SugestaoDaCd2Test(unittest.TestCase):
    def _com_is(self, na_lista):
        s = {**sugestao_do_item()["1"], "reducao": "60",
             "imposto_seletivo": {"na_lista": na_lista, "texto": IS_TEXTO}}
        return entrega(C_OK, sugestoes={"1": s})

    def test_reducao_aparece_na_linha_da_sugestao(self):
        for formato in ("html", "markdown"):
            with self.subTest(formato):
                self.assertIn("cClassTrib 200003 (CST 200), redução de 60%", render.render(self._com_is(False), formato))
        zero = entrega(C_OK, sugestoes={"1": {**sugestao_do_item()["1"], "reducao": "0"}})
        self.assertIn("redução de 0%", render.render(zero, "html"))
        sem = entrega(C_OK, sugestoes={"1": sugestao_do_item()["1"]})
        self.assertNotIn("redução de", render.render(sem, "html"))
        invalida = entrega(C_OK, sugestoes={"1": {**sugestao_do_item()["1"], "reducao": "<b>x</b>"}})
        self.assertNotIn("redução de", render.render(invalida, "html"))

    def test_imposto_seletivo_na_lista_aparece_ao_lado_do_item(self):
        for formato in ("html", "markdown"):
            with self.subTest(formato):
                self.assertIn(IS_TEXTO, render.render(self._com_is(True), formato))
                self.assertNotIn(IS_TEXTO, render.render(self._com_is(False), formato))
        zap = render.render(self._com_is(True), "whatsapp")
        self.assertIn("Imposto Seletivo: veja o item 1", zap)
        self.assertNotIn("Imposto Seletivo", render.render(self._com_is(False), "whatsapp"))
        self.assertNotIn("Imposto Seletivo", render.render(entrega(C_OK, sugestoes=sugestao_do_item()), "html"))

    def test_imposto_seletivo_e_escapado(self):
        e = self._com_is(True)
        e["sugestoes"]["1"]["imposto_seletivo"]["texto"] = "<script>x</script>"
        self.assertNotIn("<script", render.render(e, "html"))
        self.assertNotIn("<script", render.render(e, "markdown"))


# Saída de hoje, sem "observacoes", gravada antes da mudança: sem a chave (ou com ela inválida), nada muda.
SAIDA_DE_HOJE = {
    ("C_OK", "html"): "e2266d0f42fad3fbfda85ef09e8531ecd7afd4b90cfc784ebdd28e38f889915c",
    ("C_OK", "markdown"): "82d5a9894a53eeac03026e99d1abee5f0864c50bba23a52bc7fc7690b8950dc3",
    ("C_OK", "whatsapp"): "4372ce7bd76104bd15325138080b7923b86843b84824e20132b696b84ba660dc",
    ("C_MISTO", "html"): "f075155482315a2654411152b837f0c1cc64d5716f6f9b8bc2de44d92e624d01",
    ("C_MISTO", "markdown"): "2bf4920734460eb236da3cee81899c67b9064cd710ed33a9f615db512438d61b",
    ("C_MISTO", "whatsapp"): "8fca337d9a0db074a159e2b92d8963fcdcb7c6943654e4fda83f240dd32aafe7",
    ("C_422", "html"): "a9e24cb30700c2b18c3ba706e3a41bc8aa1f08818f41db7df3960d47a16e832b",
    ("C_422", "markdown"): "d0865b9472e4654778e91665f53942d2daf071646a69b4e598011d63986a87b0",
    ("C_422", "whatsapp"): "ea0994c207c05b8e72473a673790699e6bfbd826fb2d84d20a96c8aba04156f8",
    ("C_NAO_CALC", "html"): "f652cbc545e76f83bd767ef89dd08e5d1c20f2161ea4f386699049575cc10596",
    ("C_NAO_CALC", "markdown"): "8e37c71f6e5181a9467ffbc2ab436c7c869c6e60f43da5d9260c5994b57a8e80",
    ("C_NAO_CALC", "whatsapp"): "94116c7b39542b08f1ce0adb3b96c464a4bdd3030ca756c024e61e5b5b2a728b",
    ("C_INCONS", "html"): "0ad2a0fdb540ddef75603d337efe9fbaac28db871f24e30d70b2cc6242482d1e",
    ("C_INCONS", "markdown"): "1cf25143d177d09b6c0c0d8152377a2465b949d3972c081667eee436242dc7c1",
    ("C_INCONS", "whatsapp"): "eb9a82752e837c515260eb9b84e3fda9c06bd884c0414159eb1c2f8a2c856614",
    ("C_NAO_IMPL", "html"): "5bf7fac1a8b21493b2854e4e4e103d270e0777399db3ab22b42cee8a7afab1a3",
    ("C_NAO_IMPL", "markdown"): "766e636aef602472f35022ba872a6a9a0d2a28b5a06e444ba0dbcda51a712f57",
    ("C_NAO_IMPL", "whatsapp"): "f3bbb5632df75b534dc7504eda8bb03e121209217ce98270134fc48db17358c8",
    ("C_500", "html"): "a3955f8233c7b9cea653f9950eb97022b30c06a3755195a3caa27a05c703a636",
    ("C_500", "markdown"): "486924aa7cc9c75cd7a3ac570cbdeddb280aba38a73cb80a17ecde40dddb6048",
    ("C_500", "whatsapp"): "81175b655f6a63bd368d6dd9bf91903d8fb1c699a370360d6963546590487673",
    ("C_400", "html"): "7ba67d6361a379419098aaba1e59fe2728a234170637e688d2af849b456727db",
    ("C_400", "markdown"): "412a84c0299cf32ed3d564c6e166b2047488520e09f33f271f79b2039a721851",
    ("C_400", "whatsapp"): "1542de6aac4d820e455953f879d6532fd1e0a69515cddc4aab7d73f41ca77376",
    ("exemplo-sugestao", "html"): "0fa54752912254fb3193a203efc6238ce6c2531ffcfa4ee4f177614875457941",
    ("exemplo-sugestao", "markdown"): "004765ca539bc78c8ea5acfa10cabb2526d437c655b8c584300debefc28fbda3",
    ("exemplo-sugestao", "whatsapp"): "792b5718e7f16235feb03fe76a96ff11de794ff1eac941b9baa329d025b33cd6",
    ("2026", "html"): "b488cdc7825c468e4a20ddd98e2ddade03307433f9f3497c4a83df1895155d59",
    ("2026", "markdown"): "5b2d9b9957da9c3dc9e9bf9940e7de58c7eef23b20ee46197d8a1573f4fc61c1",
    ("2026", "whatsapp"): "3a2f067a371407345f3adb1c5ad8b64f4fc0786671e5a435fefd02160b90a29e",
}
OBS_IS = "O total não inclui o Imposto Seletivo do item 1 (Cerveja). A alíquota ainda não foi definida em lei."
MAIS_OBS = "Há mais observações na página completa."


def _amostras():
    for nome in ("C_OK", "C_MISTO", "C_422", "C_NAO_CALC", "C_INCONS", "C_NAO_IMPL", "C_500", "C_400"):
        yield nome, entrega(globals()[nome])
    yield "exemplo-sugestao", entrega(C_OK, exemplo=True, sugestoes=sugestao_do_item())
    yield "2026", vsn015_de_2026()


def _hash(texto):
    import hashlib
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


class ObservacoesTest(unittest.TestCase):
    def test_sem_observacoes_a_saida_e_a_de_hoje(self):
        for nome, e in _amostras():
            for formato in FORMATOS:
                with self.subTest(caso=nome, formato=formato):
                    self.assertEqual(_hash(render.render(e, formato)), SAIDA_DE_HOJE[(nome, formato)])

    def test_observacoes_invalidas_sao_ignoradas(self):
        for invalida in (None, [], "texto solto", {"1": "x"}, 7, [1, 2], [None, {"a": "b"}], ["", "   "]):
            for nome, e in _amostras():
                e = copy.deepcopy(e)
                e["observacoes"] = invalida
                for formato in FORMATOS:
                    with self.subTest(obs=repr(invalida), caso=nome, formato=formato):
                        self.assertEqual(_hash(render.render(e, formato)), SAIDA_DE_HOJE[(nome, formato)])

    def test_lista_mista_fica_so_com_os_textos(self):
        md = render.render(entrega(C_OK, observacoes=[1, OBS_IS, None]), "markdown")
        self.assertIn("## Observações", md)
        self.assertEqual(md.count("Imposto Seletivo do item 1"), 1)

    def test_bloco_nos_tres_formatos_abaixo_dos_totais_e_antes_dos_itens(self):
        e = entrega(C_OK, observacoes=[OBS_IS])
        html = render.render(e, "html")
        self.assertIn(">Observações<", html)
        self.assertIn(OBS_IS, html)
        self.assertLess(html.index("R$ 11,61"), html.index(">Observações<"))
        self.assertLess(html.index(">Observações<"), html.index("<table"))
        md = render.render(e, "markdown")
        self.assertIn("## Observações\n\n- " + OBS_IS, md)
        self.assertLess(md.index("**CBS:**"), md.index("## Observações"))
        self.assertLess(md.index("## Observações"), md.index("## Itens da operação"))
        wa = render.render(e, "whatsapp")
        self.assertIn(OBS_IS, wa)
        self.assertLess(wa.index("IBS: R$ 0,13"), wa.index(OBS_IS))
        self.assertLess(wa.index(OBS_IS), wa.index("Conta igual"))

    def test_abaixo_do_erro_da_operacao(self):
        e = entrega(C_422, observacoes=["Nota da conversa."])
        html = render.render(e, "html")
        self.assertLess(html.index("ERRO NA OPERAÇÃO"), html.index(">Observações<"))
        self.assertIn("## Observações", render.render(e, "markdown"))
        self.assertIn("Nota da conversa.", render.render(e, "whatsapp"))

    def test_no_maximo_cinco_e_cada_uma_com_300(self):
        notas = [f"N{n} " + "x" * 400 for n in range(7)]
        md = render.render(entrega(C_OK, observacoes=notas), "markdown")
        bloco = md.split("## Observações", 1)[1].split("## Itens", 1)[0]
        linhas = [l for l in bloco.splitlines() if l.startswith("- ")]
        self.assertEqual(len(linhas), 5)
        self.assertNotIn("N5 ", bloco)
        self.assertTrue(all(len(l) <= 2 + 300 for l in linhas), [len(l) for l in linhas])

    def test_whatsapp_com_cinco_notas_de_2000_cabe_e_guarda_a_primeira(self):
        notas = [f"Nota {n}: " + "y" * 2000 for n in range(5)]
        for caso in (C_OK, C_MISTO, C_422):
            e = entrega(caso, exemplo=True, sugestoes=sugestao_do_item(), observacoes=notas,
                        aviso_versao={"consultado": True, "ha_versao_nova": True, "calculadora": "1.0.3"})
            with self.subTest(caso):
                wa = render.render(e, "whatsapp")
                self.assertLessEqual(len(wa), 1000)
                self.assertIn("Nota 0: yyy", wa)
                self.assertIn(MAIS_OBS, wa)
                self.assertNotIn("Nota 1:", wa)
                self.assertIn(AVISO_FIXO, wa)
                self.assertIn("Conta igual", wa)
                if caso != C_422:
                    self.assertIn("CBS: R$", wa)
                    self.assertIn("IBS: R$", wa)

    def test_whatsapp_com_notas_curtas_mostra_todas(self):
        wa = render.render(entrega(C_OK, observacoes=[f"Nota {n}." for n in range(5)]), "whatsapp")
        for n in range(5):
            self.assertIn(f"Nota {n}.", wa)
        self.assertNotIn(MAIS_OBS, wa)

    def test_observacao_passa_pelo_limpador_de_cada_formato(self):
        e = entrega(C_OK, observacoes=[HOSTIL])
        html = render.render(e, "html")
        for tag in ("<script", "<img src=x"):
            self.assertNotIn(tag, html)
        for formato in ("markdown", "whatsapp"):
            texto = render.render(e, formato)
            for proibido in ("<", ">", "`", "javascript:"):
                self.assertNotIn(proibido, texto, formato)


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
