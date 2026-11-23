"""Testes do comparador com a calculadora oficial (scripts/receita_paridade.py).

O servidor oficial é substituído por um http.server de mentira numa thread.
"""
import contextlib
import decimal
import http.server
import importlib.util
import io
import json
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
D = decimal.Decimal


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


paridade = load_module("receita_paridade", ROOT / "scripts" / "receita_paridade.py")


class Oficial:
    """Servidor de mentira. `respostas` mapeia (metodo, caminho) para (status, tipo, bytes)."""

    def __init__(self, respostas):
        self.respostas = respostas
        self.recebidos = []
        oficial = self

        class Manipulador(http.server.BaseHTTPRequestHandler):
            def _responder(self):
                tamanho = int(self.headers.get("Content-Length") or 0)
                corpo = self.rfile.read(tamanho) if tamanho else b""
                oficial.recebidos.append((self.command, self.path, corpo, self.headers.get("Content-Type")))
                status, tipo, dados = oficial.respostas.get((self.command, self.path), (404, None, b""))
                self.send_response(status)
                if tipo:
                    self.send_header("Content-Type", tipo)
                self.send_header("Content-Length", str(len(dados)))
                self.end_headers()
                self.wfile.write(dados)

            do_GET = _responder
            do_POST = _responder

            def log_message(self, *args):
                pass

        self.servidor = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Manipulador)
        self.url = f"http://127.0.0.1:{self.servidor.server_address[1]}/api"
        self.thread = threading.Thread(target=self.servidor.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.servidor.shutdown()
        self.servidor.server_close()
        self.thread.join(5)


def json_bytes(texto):
    return texto.encode("utf-8")


class TestChamarOficial(unittest.TestCase):
    def test_post_envia_json_e_le_corpo(self):
        with Oficial({("POST", "/api/calculadora/x"): (200, "application/json", json_bytes('{"a": 1.00}'))}) as of:
            resp = paridade.chamar_oficial(
                of.url, {"metodo": "POST", "caminho": "/calculadora/x", "corpo": {"id": "é"}})
            self.assertEqual(resp, {"status": 200, "tipo": "application/json", "corpo": {"a": D("1.00")}})
            metodo, caminho, corpo, tipo = of.recebidos[0]
            self.assertEqual(json.loads(corpo.decode("utf-8")), {"id": "é"})
            self.assertEqual(tipo, "application/json")

    def test_get_com_query_e_sem_corpo(self):
        with Oficial({("GET", "/api/atividades?data=2027-01-01"): (200, "application/json", b"[]")}) as of:
            resp = paridade.chamar_oficial(
                of.url, {"metodo": "GET", "caminho": "/atividades?data=2027-01-01", "corpo": None})
            self.assertEqual(resp["corpo"], [])
            self.assertEqual(of.recebidos[0][2], b"")

    def test_status_de_erro_vem_com_o_corpo(self):
        dados = json_bytes('{"status": 422}')
        with Oficial({("POST", "/api/p"): (422, "application/problem+json", dados)}) as of:
            resp = paridade.chamar_oficial(of.url, {"metodo": "POST", "caminho": "/p", "corpo": {}})
            self.assertEqual(resp["status"], 422)
            self.assertEqual(resp["tipo"], "application/problem+json")
            self.assertEqual(resp["corpo"], {"status": 422})

    def test_corpo_vazio_vira_none(self):
        with Oficial({}) as of:
            resp = paridade.chamar_oficial(of.url, {"metodo": "GET", "caminho": "/nada", "corpo": None})
            self.assertEqual(resp, {"status": 404, "tipo": None, "corpo": None})

    def test_corpo_que_nao_e_json_vira_texto(self):
        with Oficial({("GET", "/api/t"): (500, "text/plain;charset=UTF-8", "falhou: ç".encode("utf-8"))}) as of:
            resp = paridade.chamar_oficial(of.url, {"metodo": "GET", "caminho": "/t", "corpo": None})
            self.assertEqual(resp["corpo"], "falhou: ç")
            self.assertEqual(resp["tipo"], "text/plain;charset=UTF-8")

    def test_json_invalido_com_tipo_json_vira_texto(self):
        with Oficial({("GET", "/api/q"): (200, "application/json", b"{quebrado")}) as of:
            resp = paridade.chamar_oficial(of.url, {"metodo": "GET", "caminho": "/q", "corpo": None})
            self.assertEqual(resp["corpo"], "{quebrado")

    def test_numeros_json_guardam_a_forma_escrita(self):
        with Oficial({("GET", "/api/n"): (200, "application/json", b'{"a": 1.0, "b": 1.00, "c": 1, "d": 1E+2}')}) as of:
            corpo = paridade.chamar_oficial(of.url, {"metodo": "GET", "caminho": "/n", "corpo": None})["corpo"]
            self.assertEqual([repr(corpo[k]) for k in "abcd"],
                             ["Decimal('1.0')", "Decimal('1.00')", "1", "Decimal('1E+2')"])

    def test_fora_do_ar_diz_a_url(self):
        with Oficial({}) as of:
            url = of.url
        with self.assertRaisesRegex(paridade.OficialIndisponivel, "127.0.0.1"):
            paridade.chamar_oficial(url, {"metodo": "GET", "caminho": "/x", "corpo": None}, prazo=2)


class TestIdaEVoltaDoJson(unittest.TestCase):
    TEXTO = ('{"a": 1.0, "b": 1.00, "c": 1, "d": 1E+2, "e": -0.0, "f": 12345678901234567890123, '
             '"g": 0.1234567890123456789012345678901234, "h": "x\\u00e9\\n\\"", "i": [true, null, {"j": 2.50}]}')

    def test_ler_e_escrever_devolvem_os_mesmos_valores(self):
        lido = paridade.ler_json(self.TEXTO)
        relido = paridade.ler_json(paridade.escrever_json(lido))
        self.assertEqual(repr(relido), repr(lido))
        self.assertEqual(repr(relido["a"]), "Decimal('1.0')")
        self.assertEqual(repr(relido["b"]), "Decimal('1.00')")
        self.assertEqual(repr(relido["e"]), "Decimal('-0.0')")
        self.assertIsInstance(relido["c"], int)
        self.assertIs(relido["i"][0], True)

    def test_escrita_e_estavel(self):
        lido = paridade.ler_json(self.TEXTO)
        primeiro = paridade.escrever_json(lido)
        self.assertEqual(paridade.escrever_json(paridade.ler_json(primeiro)), primeiro)

    def test_1_0_e_1_00_continuam_diferentes_no_arquivo(self):
        texto = paridade.escrever_json({"a": D("1.0"), "b": D("1.00")})
        self.assertIn("1.0,", texto.replace("\n", ""))
        self.assertIn("1.00", texto)

    def test_escrita_e_utf8_sem_escapar_acento(self):
        self.assertIn("é", paridade.escrever_json({"a": "é"}))


class TestLimpar(unittest.TestCase):
    def test_remove_campo_simples_sem_mexer_no_original(self):
        corpo = {"a": 1, "b": {"c": 2, "d": 3}}
        self.assertEqual(paridade.limpar(corpo, ["b/c"]), {"a": 1, "b": {"d": 3}})
        self.assertEqual(corpo, {"a": 1, "b": {"c": 2, "d": 3}})

    def test_curinga_em_lista(self):
        corpo = {"objetos": [{"x": 1, "y": 2}, {"x": 3, "y": 4}]}
        self.assertEqual(paridade.limpar(corpo, ["objetos/*/x"]), {"objetos": [{"y": 2}, {"y": 4}]})

    def test_indice_e_caminho_inexistente(self):
        corpo = {"l": [{"x": 1}, {"x": 2}]}
        self.assertEqual(paridade.limpar(corpo, ["l/1/x", "nao/existe", "l/9/x"]), {"l": [{"x": 1}, {}]})

    def test_corpo_que_nao_e_estrutura_passa_direto(self):
        self.assertEqual(paridade.limpar("texto", ["a"]), "texto")
        self.assertIsNone(paridade.limpar(None, ["a"]))


def resp(corpo, status=200):
    return {"status": status, "corpo": corpo}


class TestComparar(unittest.TestCase):
    def test_iguais_nao_tem_diferenca(self):
        a = resp({"a": [1, {"b": "x"}], "c": D("1.50")})
        self.assertEqual(paridade.comparar(a, json.loads(json.dumps({"a": [1, {"b": "x"}]})) and
                                           resp({"a": [1, {"b": "x"}], "c": D("1.50")}), []), [])

    def test_diferenca_diz_o_caminho(self):
        oficial = resp({"objetos": [{"tribCalc": {"vCBS": "1.00"}}]})
        local = resp({"objetos": [{"tribCalc": {"vCBS": "1.01"}}]})
        self.assertEqual(paridade.comparar(oficial, local, []),
                         ["objetos/0/tribCalc/vCBS: '1.00' != '1.01'"])

    def test_campo_ignorado_nao_conta(self):
        oficial = resp({"a": 1, "hora": "10:00", "l": [{"id": 1, "v": 1}]})
        local = resp({"a": 1, "hora": "10:01", "l": [{"id": 2, "v": 1}]})
        self.assertEqual(paridade.comparar(oficial, local, ["hora", "l/*/id"]), [])
        self.assertEqual(len(paridade.comparar(oficial, local, [])), 2)

    def test_curinga_em_lista(self):
        oficial = resp({"objetos": [{"x": 1, "v": "a"}, {"x": 2, "v": "b"}]})
        local = resp({"objetos": [{"x": 9, "v": "a"}, {"x": 8, "v": "c"}]})
        self.assertEqual(paridade.comparar(oficial, local, ["objetos/*/x"]), ["objetos/1/v: 'b' != 'c'"])

    def test_status_diferente_e_diferenca(self):
        self.assertEqual(paridade.comparar(resp({"a": 1}, 200), resp({"a": 1}, 422), []),
                         ["status: 200 != 422"])

    def test_ordem_de_chaves_nao_conta_e_ordem_de_lista_conta(self):
        self.assertEqual(paridade.comparar(resp({"a": 1, "b": 2}), resp({"b": 2, "a": 1}), []), [])
        self.assertEqual(paridade.comparar(resp({"l": [1, 2]}), resp({"l": [2, 1]}), []),
                         ["l/0: 1 != 2", "l/1: 2 != 1"])

    def test_texto_numerico_compara_como_texto(self):
        self.assertEqual(paridade.comparar(resp({"v": "1.0"}), resp({"v": "1.00"}), []),
                         ["v: '1.0' != '1.00'"])

    def test_numero_json_compara_pela_forma_escrita(self):
        self.assertEqual(paridade.comparar(resp({"v": D("1.0")}), resp({"v": D("1.00")}), []),
                         ["v: 1.0 != 1.00"])
        self.assertEqual(paridade.comparar(resp({"v": D("1.00")}), resp({"v": D("1.00")}), []), [])
        self.assertEqual(paridade.comparar(resp({"v": 1}), resp({"v": D("1.0")}), []), ["v: 1 != 1.0"])

    def test_booleano_nao_e_numero(self):
        self.assertEqual(paridade.comparar(resp({"v": True}), resp({"v": 1}), []), ["v: True != 1"])
        self.assertEqual(paridade.comparar(resp({"v": 0}), resp({"v": False}), []), ["v: 0 != False"])

    def test_campo_ausente_de_um_lado(self):
        self.assertEqual(paridade.comparar(resp({"a": 1, "b": 2}), resp({"a": 1}), []),
                         ["b: ausente no local"])
        self.assertEqual(paridade.comparar(resp({"a": 1}), resp({"a": 1, "b": 2}), []),
                         ["b: ausente no oficial"])

    def test_null_e_diferente_de_ausente(self):
        self.assertEqual(paridade.comparar(resp({"a": None}), resp({}), []), ["a: ausente no local"])

    def test_listas_de_tamanhos_diferentes(self):
        self.assertEqual(paridade.comparar(resp({"l": [1, 2, 3]}), resp({"l": [1, 2]}), []),
                         ["l: tamanho 3 != 2", "l/2: ausente no local"])

    def test_tipos_diferentes(self):
        self.assertEqual(paridade.comparar(resp({"a": {"b": 1}}), resp({"a": [1]}), []),
                         ["a: {'b': 1} != [1]"])

    def test_corpo_vazio_e_texto(self):
        self.assertEqual(paridade.comparar(resp(None, 404), resp(None, 404), []), [])
        self.assertEqual(paridade.comparar(resp("erro", 500), resp("outro", 500), []), ["corpo: 'erro' != 'outro'"])
        self.assertEqual(paridade.comparar(resp(None, 404), resp({}, 404), []), ["corpo: None != {}"])

    def test_tipo_do_conteudo_nao_entra_na_comparacao(self):
        oficial = {"status": 200, "tipo": "application/json", "corpo": {}}
        self.assertEqual(paridade.comparar(oficial, {"status": 200, "corpo": {}}, []), [])


class TestRodar(unittest.TestCase):
    def casos(self):
        return [
            {"id": "um", "grupo": "item", "requisicao": {"metodo": "GET", "caminho": "/um", "corpo": None}},
            {"id": "dois", "grupo": "item", "requisicao": {"metodo": "GET", "caminho": "/dois", "corpo": None}},
        ]

    def test_conta_iguais_e_divergentes(self):
        dados = {"/um": b'{"v": "a"}', "/dois": b'{"v": "b"}'}
        with Oficial({("GET", "/api" + c): (200, "application/json", d) for c, d in dados.items()}) as of:
            def local(req):
                return {"status": 200, "corpo": {"v": "a" if req["caminho"] == "/um" else "z"}}
            relatorio = paridade.rodar(self.casos(), of.url, local, [])
        self.assertEqual(relatorio["casos"], 2)
        self.assertEqual(relatorio["iguais"], 1)
        self.assertEqual(relatorio["divergentes"], [{"id": "dois", "diferencas": ["v: 'b' != 'z'"]}])

    def test_ignorar_vale_no_rodar(self):
        with Oficial({("GET", "/api/um"): (200, "application/json", b'{"v": 1, "t": "x"}')}) as of:
            relatorio = paridade.rodar(self.casos()[:1], of.url,
                                       lambda req: {"status": 200, "corpo": {"v": 1, "t": "y"}}, ["t"])
        self.assertEqual(relatorio["iguais"], 1)

    def test_local_que_quebra_vira_divergencia(self):
        with Oficial({("GET", "/api/um"): (200, "application/json", b"{}")}) as of:
            def local(req):
                raise ValueError("deu ruim")
            relatorio = paridade.rodar(self.casos()[:1], of.url, local, [])
        self.assertEqual(relatorio["iguais"], 0)
        self.assertEqual(relatorio["divergentes"][0]["id"], "um")
        self.assertIn("deu ruim", relatorio["divergentes"][0]["diferencas"][0])


class TestCli(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="paridade_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        pasta = self.tmp / "conferencia" / "demo"
        pasta.mkdir(parents=True)
        self.pasta = pasta
        (pasta / "config.json").write_text(json.dumps({"skill": "demo", "ignorar": []}), encoding="utf-8")
        (pasta / "meta.json").write_text(json.dumps({"skill": "demo", "base": {"versao": "V1"}}), encoding="utf-8")
        casos = [
            {"id": "calc", "grupo": "item",
             "requisicao": {"metodo": "POST", "caminho": "/calc", "corpo": {"v": "1.0"}}},
            {"id": "lista", "grupo": "consulta",
             "requisicao": {"metodo": "GET", "caminho": "/lista?data=2027-01-01", "corpo": None}},
            {"id": "sumiu", "grupo": "erro",
             "requisicao": {"metodo": "GET", "caminho": "/sumiu", "corpo": None}},
        ]
        (pasta / "casos-escritos.json").write_text(json.dumps(casos), encoding="utf-8")
        self.respostas = {
            ("POST", "/api/calc"): (200, "application/json", b'{"total": 1.50, "ok": true}'),
            ("GET", "/api/lista?data=2027-01-01"): (200, "application/json", b"[1, 2]"),
        }

    def executar(self, *args):
        saida, erro = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(saida), contextlib.redirect_stderr(erro):
            codigo = paridade.main(["--raiz", str(self.tmp), *args])
        return codigo, saida.getvalue(), erro.getvalue()

    def test_gravar_esperadas_guarda_status_e_corpo(self):
        with Oficial(self.respostas) as of:
            codigo, _, _ = self.executar("--skill", "demo", "--oficial", of.url, "--gravar-esperadas")
        self.assertEqual(codigo, 0)
        esperadas = paridade.ler_json((self.pasta / "esperadas.json").read_text(encoding="utf-8"))
        self.assertEqual(esperadas["meta"], {"skill": "demo", "base": {"versao": "V1"}})
        self.assertEqual([c["id"] for c in esperadas["casos"]], ["calc", "lista", "sumiu"])
        calc, lista, sumiu = esperadas["casos"]
        self.assertEqual(calc["grupo"], "item")
        self.assertEqual(calc["requisicao"], {"metodo": "POST", "caminho": "/calc", "corpo": {"v": "1.0"}})
        self.assertEqual(repr(calc["resposta"]["corpo"]), repr({"total": D("1.50"), "ok": True}))
        self.assertEqual(calc["resposta"]["status"], 200)
        self.assertEqual(calc["resposta"]["tipo"], "application/json")
        self.assertEqual(lista["resposta"]["corpo"], [1, 2])
        self.assertEqual(sumiu["resposta"], {"status": 404, "tipo": None, "corpo": None})

    def test_gravar_nao_compara_nem_precisa_de_bateria(self):
        with Oficial(self.respostas) as of:
            codigo, saida, _ = self.executar("--skill", "demo", "--oficial", of.url, "--gravar-esperadas")
        self.assertEqual(codigo, 0)
        self.assertIn("3", saida)

    def test_oficial_fora_do_ar_sai_com_erro_claro(self):
        with Oficial({}) as of:
            url = of.url
        codigo, _, erro = self.executar("--skill", "demo", "--oficial", url, "--gravar-esperadas")
        self.assertEqual(codigo, 2)
        self.assertIn("127.0.0.1", erro)
        self.assertFalse((self.pasta / "esperadas.json").exists())

    def test_sem_casos_escritos_diz_o_que_falta(self):
        (self.pasta / "casos-escritos.json").unlink()
        codigo, _, erro = self.executar("--skill", "demo", "--oficial", "http://127.0.0.1:9/api",
                                        "--gravar-esperadas")
        self.assertEqual(codigo, 2)
        self.assertIn("casos-escritos.json", erro)

    def gravar_e_preparar_bateria(self, corpo_calc):
        with Oficial(self.respostas) as of:
            self.executar("--skill", "demo", "--oficial", of.url, "--gravar-esperadas")
        (self.pasta / "bateria.py").write_text(
            "def responder(req):\n"
            "    if req['caminho'] == '/calc':\n"
            f"        return {{'status': 200, 'corpo': {corpo_calc}}}\n"
            "    if req['caminho'].startswith('/lista'):\n"
            "        return {'status': 200, 'corpo': [1, 2]}\n"
            "    return {'status': 404, 'corpo': None}\n", encoding="utf-8")

    def test_comparar_sai_zero_so_se_tudo_igual(self):
        self.gravar_e_preparar_bateria("{'total': __import__('decimal').Decimal('1.50'), 'ok': True}")
        with Oficial(self.respostas) as of:
            codigo, saida, _ = self.executar("--skill", "demo", "--oficial", of.url, "--completa")
        self.assertEqual(codigo, 0, saida)
        self.assertIn("3 casos, 3 iguais", saida)

    def test_comparar_com_divergencia_sai_um_e_lista(self):
        self.gravar_e_preparar_bateria("{'total': __import__('decimal').Decimal('1.5'), 'ok': True}")
        with Oficial(self.respostas) as of:
            codigo, saida, _ = self.executar("--skill", "demo", "--oficial", of.url, "--completa")
        self.assertEqual(codigo, 1)
        self.assertIn("calc", saida)
        self.assertIn("total: 1.50 != 1.5", saida)

    def test_amostra_limita_o_numero_de_casos(self):
        self.gravar_e_preparar_bateria("{'total': __import__('decimal').Decimal('1.50'), 'ok': True}")
        with Oficial(self.respostas) as of:
            codigo, saida, _ = self.executar("--skill", "demo", "--oficial", of.url, "--amostra", "2")
        self.assertEqual(codigo, 0)
        self.assertIn("2 casos, 2 iguais", saida)

    def test_sem_bateria_diz_o_que_falta(self):
        with Oficial(self.respostas) as of:
            self.executar("--skill", "demo", "--oficial", of.url, "--gravar-esperadas")
            codigo, _, erro = self.executar("--skill", "demo", "--oficial", of.url, "--completa")
        self.assertEqual(codigo, 2)
        self.assertIn("bateria.py", erro)


if __name__ == "__main__":
    unittest.main()
