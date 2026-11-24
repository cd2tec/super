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

    def test_sem_item_calculado_nao_ha_selo(self):
        for caso in (C_NAO_CALC, C_422, C_400):
            for formato in FORMATOS:
                with self.subTest(caso=caso, formato=formato):
                    texto = render.render(entrega(caso, sugestoes=sugestao_do_item()), formato)
                    self.assertNotIn("Conta igual", texto)
                    self.assertNotIn(render.SELO_SUGESTAO, texto)
                    self.assertIn(render.SEM_SELO, texto)
        for formato in FORMATOS:
            with self.subTest(caso=C_OK, formato=formato):
                texto = render.render(entrega(C_OK), formato)
                self.assertIn("Conta igual", texto)
                self.assertNotIn(render.SEM_SELO, texto)

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
                if nome == "numeros":
                    self.assertTrue("Conta igual" in texto)
                elif nome == "2026-exemplo":
                    # nenhum item calculado: sem selo, nem no pior caso
                    self.assertNotIn("Conta igual", texto)

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
        # sem efeito_no_simples (entrega antiga): a redução sai, dita como do regime regular
        for formato in ("html", "markdown"):
            with self.subTest(formato):
                self.assertIn("cClassTrib 200003 (CST 200), redução de 60% no regime regular",
                              render.render(self._com_is(False), formato))
        zero = entrega(C_OK, sugestoes={"1": {**sugestao_do_item()["1"], "reducao": "0"}})
        self.assertNotIn("redução de", render.render(zero, "html"))
        sem = entrega(C_OK, sugestoes={"1": sugestao_do_item()["1"]})
        self.assertNotIn("redução de", render.render(sem, "html"))
        invalida = entrega(C_OK, sugestoes={"1": {**sugestao_do_item()["1"], "reducao": "<b>x</b>"}})
        self.assertNotIn("redução de", render.render(invalida, "html"))

    def test_reducao_sem_efeito_no_simples_e_dita_assim(self):
        def com(reducao, efeito, texto=""):
            s = {**sugestao_do_item()["1"], "reducao": reducao, "efeito_no_simples": efeito, "efeito_texto": texto}
            return entrega(C_OK, sugestoes={"1": s})
        for formato in ("html", "markdown"):
            with self.subTest(formato):
                pagina = render.render(com("100", "nenhum"), formato)
                self.assertIn("cClassTrib 200003 (CST 200), redução de 100% no regime regular, sem efeito nesta conta",
                              pagina)
                zero = render.render(com("0", "nenhum"), formato)
                self.assertNotIn("redução de", zero)
                self.assertIn("cClassTrib 200003 (CST 200): Cesta", zero)
                outro = render.render(com("60", "muda o tipo de receita", "Pode mudar o resultado desta conta."), formato)
                self.assertNotIn("redução de", outro)
                self.assertIn("Pode mudar o resultado desta conta.", outro)

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
    ("C_OK", "html"): "5910839e34777af96fbfc92eb4f7c206842366feaf75d8d767811b19cf68c1d9",
    ("C_OK", "markdown"): "82d5a9894a53eeac03026e99d1abee5f0864c50bba23a52bc7fc7690b8950dc3",
    ("C_OK", "whatsapp"): "4372ce7bd76104bd15325138080b7923b86843b84824e20132b696b84ba660dc",
    ("C_MISTO", "html"): "90cee3eebc9859743082eb730a3fd631a602ea18d756d5c25bc551dea9f31491",
    ("C_MISTO", "markdown"): "2bf4920734460eb236da3cee81899c67b9064cd710ed33a9f615db512438d61b",
    ("C_MISTO", "whatsapp"): "8fca337d9a0db074a159e2b92d8963fcdcb7c6943654e4fda83f240dd32aafe7",
    ("C_422", "html"): "ffb8ffc6222caff8f668b520247d0b97642ee36d6950e91f0278d9eb1b8aafe3",
    ("C_422", "markdown"): "dd16440f0a051222f50137a58311c883c46840c0f917a35579cf44f772deed54",
    ("C_422", "whatsapp"): "50c1065083d471a0986098a4a6db1b7ba952a0cdfb0ee39888dc8c97503d3293",
    ("C_NAO_CALC", "html"): "5dac59b3be8b9cc353d1ae46e26eba105ce13bbd19d399f2688ab0957497ff35",
    ("C_NAO_CALC", "markdown"): "ddba0da277beffb50f411e0da72382c5f00e22c4ba2dcd91a7fa74eb62142163",
    ("C_NAO_CALC", "whatsapp"): "a9ce877a54bd35da92ca5adad78b34f8b52867310fc0aade8b3a69b43e5dd38f",
    ("C_INCONS", "html"): "a25350668b670cd63abdbc7e6ae96a43006e3e4e07760ce9804cce220adbe783",
    ("C_INCONS", "markdown"): "3673d4531fd924b021e73180f14bbed77fa9b372187145a4f6eecc16593ff695",
    ("C_INCONS", "whatsapp"): "0267f4e234d1134eed52cf4184fe2256ba3a9bd9a8c99baec72e45fa06d230c1",
    ("C_NAO_IMPL", "html"): "7bb77d5f263adf987b909db04f8e3bfb64446d625da4de3801a77854f309fdc8",
    ("C_NAO_IMPL", "markdown"): "2ab101496a39308d4d301d9adf4b8fbf445861a3da72a601f71b7dbffc013e9a",
    ("C_NAO_IMPL", "whatsapp"): "ab82d9aa504623e9cc427526e2225d83f886de4f5a8e91c19199b16dcc2a1f39",
    ("C_500", "html"): "2514787ec603c751733d8fd68b6738156616dd89650a15f688b4280fb8893f0a",
    ("C_500", "markdown"): "3eb3b2f4566bb362b5cc8e0f0efe99cfefb174eb6e01cbbb148c3ecbf5cd6465",
    ("C_500", "whatsapp"): "18eca5dea2fce85c540b3592cf4212bff61f243031ea85b8c5ff4742350ffbaf",
    ("C_400", "html"): "306801711d6faa30ff66b8fba81d186be4e70674686559c47b5bba2c98e41612",
    ("C_400", "markdown"): "c9cc2e617721e14b2ff0973ff484f8962757e79efb3093eeb9b3483a0cc4684d",
    ("C_400", "whatsapp"): "d3e50f55b3ebd5c066228aeb1b9506bb3c093852dd679921f183053754e551a1",
    ("exemplo-sugestao", "html"): "2d3609e6f64d7562a91cbd2330facc3409ce001a8523eda190ae40100dbdb82b",
    ("exemplo-sugestao", "markdown"): "004765ca539bc78c8ea5acfa10cabb2526d437c655b8c584300debefc28fbda3",
    ("exemplo-sugestao", "whatsapp"): "792b5718e7f16235feb03fe76a96ff11de794ff1eac941b9baa329d025b33cd6",
    ("2026", "html"): "b90a76dca0842704ac84fe62e5f3812be73b6efff833aaacdd17850fd0a5552b",
    ("2026", "markdown"): "ec95122bbb9b0df8689b95728da685baca6f7687426fa6c8d5392d608de3a37f",
    ("2026", "whatsapp"): "e51ed547ee967aa519e4014d456cc3a623559f442ab4d46be9d73ecf6c1eecfd",
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
                if caso == C_422:
                    self.assertNotIn("Conta igual", wa)  # erro na operação: nenhum item calculado
                else:
                    self.assertIn("Conta igual", wa)
                if caso != C_422:
                    self.assertIn("CBS: R$", wa)
                    self.assertIn("IBS: R$", wa)

    def test_whatsapp_com_observacao_sempre_traz_o_total_de_tributos(self):
        # a observação diz "o total não inclui...": o total tem de estar no texto, e o limite de 1000 vale
        for caso in (C_OK, C_MISTO):
            total = entrega(caso)["calculo"]["corpo"]["total"]["totalTributos"]
            for notas in ([OBS_IS], [OBS_IS] * 5, [f"Nota {n}: " + "y" * 2000 for n in range(5)]):
                e = entrega(caso, exemplo=True, sugestoes=sugestao_do_item(), observacoes=notas,
                            aviso_versao={"consultado": True, "ha_versao_nova": True, "calculadora": "1.0.3"})
                e = copy.deepcopy(e)
                e["operacao"]["id"] = "Z" * 2000
                with self.subTest(caso=caso, notas=len(notas[0])):
                    wa = render.render(e, "whatsapp")
                    self.assertLessEqual(len(wa), 1000)
                    self.assertIn(f"Total: {render._brl(total)}", wa)
        sem = render.render(entrega(C_OK), "whatsapp")
        self.assertLessEqual(len(sem), 1000)

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
