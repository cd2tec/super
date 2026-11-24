"""A bateria da conferência do regime regular: a forma dos casos, os grupos e as respostas oficiais gravadas.

A paridade do motor com o esperadas.json é dos testes da skill (test_calc_rr.py, ligado grupo a grupo). Aqui só se
confere que a bateria importa sem o calc.py, gera os casos de sempre e que as esperadas batem com ela.
"""
import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
from versao_da_base import base_completa_da_skill  # noqa: E402

SKILL = "cbs-ibs-regime-regular"
PASTA = ROOT / "conferencia" / SKILL
INDICE_COMPRA_GOV = 3  # a primeira nota de compra governamental do lote, na semente padrão do Java
AMOSTRA = 600  # o --amostra com que o esperadas.json foi gravado
CAMINHOS = {"/calculadora/regime-geral", "/calculadora/observabilidade/regime-geral"}


def _bateria():
    nome = "bateria_rr"
    if nome not in sys.modules:
        spec = importlib.util.spec_from_file_location(nome, PASTA / "bateria.py")
        modulo = importlib.util.module_from_spec(spec)
        sys.modules[nome] = modulo
        spec.loader.exec_module(modulo)
    return sys.modules[nome]


class CasosTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.b = _bateria()
        cls.casos = cls.b.casos()

    def test_grupos_fixos(self):
        self.assertEqual(self.b.GRUPOS, ("entrada", "nomenclatura", "cbs-ibs", "reducao", "diferimento",
                                         "tributacao-regular", "is", "memoria", "totais", "regime-geral"))

    def test_casos_tem_a_forma_combinada(self):
        for caso in self.casos:
            with self.subTest(caso=caso.get("id")):
                self.assertIsInstance(caso["id"], str)
                self.assertIn(caso["grupo"], self.b.GRUPOS)
                self.assertIn(caso["origem"], ("escrito", "gerado"))
                requisicao = caso["requisicao"]
                self.assertEqual(requisicao["metodo"], "POST")
                self.assertIn(requisicao["caminho"], CAMINHOS)
                self.assertIn("corpo", requisicao)

    def test_ids_unicos(self):
        ids = [c["id"] for c in self.casos]
        self.assertEqual(len(ids), len(set(ids)))

    def test_todo_grupo_tem_escritos_e_gerados(self):
        for origem in ("escrito", "gerado"):
            grupos = {c["grupo"] for c in self.casos if c["origem"] == origem}
            self.assertEqual(grupos, set(self.b.GRUPOS), origem)

    def test_fora_do_escopo_nao_entra(self):
        # NBS, gCompraGov e tpDoc que não seja 55 nem 65 a skill recusa antes do motor
        for caso in self.casos:
            corpo = caso["requisicao"]["corpo"]
            if not isinstance(corpo, dict):
                continue
            with self.subTest(caso=caso["id"]):
                self.assertNotIn("gCompraGov", corpo)
                self.assertIn(corpo.get("tpDoc"), (None, 55, 65))
                for item in corpo.get("itens") or []:
                    if isinstance(item, dict):
                        # "nbs": null passa: os JSONs da Receita o trazem (ObservabilidadeControllerTest, validacao_88).
                        # A recusa de NBS do calc.py precisa tratar null como ausente.
                        self.assertFalse(item.get("nbs"))

    def test_mesma_lista_sempre(self):
        self.assertEqual(self.b.casos(), self.casos)
        self.assertNotEqual([c["requisicao"] for c in self.b.casos(semente=8)],
                            [c["requisicao"] for c in self.casos])

    def test_amostra_leva_todos_os_escritos_e_todo_tipo(self):
        amostra = self.b.amostra(self.casos, AMOSTRA)
        self.assertEqual(len(amostra), AMOSTRA)
        escritos = {c["id"] for c in self.casos if c["origem"] == "escrito"}
        self.assertLessEqual(escritos, {c["id"] for c in amostra})
        tipos = {self.b.tipo(c) for c in self.casos if c["origem"] == "gerado"}
        self.assertEqual({self.b.tipo(c) for c in amostra if c["origem"] == "gerado"}, tipos)
        self.assertEqual(self.b.amostra(self.casos, AMOSTRA), amostra)

    def test_amostra_pequena_demais_avisa(self):
        with self.assertRaises(ValueError):
            self.b.amostra(self.casos, 400)

    def test_tipo_tira_os_codigos(self):
        caso = {"chave": "is:sem-grupo-22030000", "requisicao": {"caminho": "/calculadora/observabilidade/regime-geral"}}
        self.assertEqual(self.b.tipo(caso), "is:sem-grupo-2203:obs")
        caso = {"chave": "nomenclatura:cst-410-com-000001-2027-01-01", "requisicao": {"caminho": "/calculadora/regime-geral"}}
        self.assertEqual(self.b.tipo(caso), "nomenclatura:cst-410-com-#-2027-01-01:rg")
        caso = {"chave": "regime-geral:lote-17-2026-banco", "requisicao": {"caminho": "/calculadora/regime-geral"}}
        self.assertEqual(self.b.tipo(caso), "regime-geral:lote-2026-banco:rg")

    def test_lote_portado_do_java(self):
        # java.util.Random(42).nextInt(100), Random(42).nextDouble() e Random(7).nextInt(7), tirados do Java 25
        self.assertEqual(self.b._JavaRandom(42).next_int(100), 30)
        self.assertEqual(self.b._JavaRandom(42).next_double(), 0.7275636800328681)
        self.assertEqual(self.b._JavaRandom(7).next_int(7), 3)
        # as notas saem iguais às do GeradorLoteArredondamento.java (conferido nota a nota nas 120 primeiras);
        # as de compra governamental ficam fora
        perfil, corpo = self.b._nota_do_lote(0)
        self.assertEqual((perfil, corpo["id"], corpo["tpDoc"], corpo["uf"]), ("2026-banco", "lote-0", 55, "AC"))
        self.assertIsNone(self.b._nota_do_lote(INDICE_COMPRA_GOV))

    def test_responder_carrega_o_calc_so_quando_chamado(self):
        if (ROOT / "core" / "cd2-super" / "skills" / SKILL / "scripts" / "calc.py").exists():
            self.skipTest("o calc.py já existe; o carregamento tardio só importa antes dele")
        self.assertNotIn("rr_calc", sys.modules)
        with self.assertRaises(FileNotFoundError):
            self.b.responder({"metodo": "POST", "caminho": "/calculadora/regime-geral", "corpo": {}})

    def test_base_completa_da_os_mesmos_casos(self):
        completa = base_completa_da_skill(SKILL)
        if completa is None:
            self.skipTest("sem a base completa do regime regular (CD2_BASE_COMPLETA e CD2_BASE_COMPLETA_SKILL)")
        self.assertEqual(self.b.casos(completa), self.casos)


class EsperadasTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.b = _bateria()
        rp = cls.b.receita_paridade
        cls.esperadas = rp.ler_json((PASTA / "esperadas.json").read_text(encoding="utf-8"))
        cls.por_id = {c["id"]: c for c in cls.b.casos()}

    def test_meta_igual_ao_da_conferencia(self):
        meta = json.loads((PASTA / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(json.loads(json.dumps(self.esperadas["meta"], default=str)), meta)

    def test_todo_grupo_tem_respostas_gravadas(self):
        grupos = {}
        for caso in self.esperadas["casos"]:
            grupos[caso["grupo"]] = grupos.get(caso["grupo"], 0) + 1
        self.assertEqual(set(grupos), set(self.b.GRUPOS))
        for grupo, quantos in grupos.items():
            self.assertGreaterEqual(quantos, 10, grupo)

    def test_esperadas_sao_casos_da_bateria(self):
        rp = self.b.receita_paridade
        for caso in self.esperadas["casos"]:
            with self.subTest(caso=caso["id"]):
                self.assertIn(caso["id"], self.por_id)
                self.assertEqual(rp.escrever_json(caso["requisicao"], None),
                                 rp.escrever_json(self.por_id[caso["id"]]["requisicao"], None))
                self.assertEqual(caso["grupo"], self.por_id[caso["id"]]["grupo"])
                self.assertIn("status", caso["resposta"])

    def test_esperadas_cobrem_todo_tipo_de_caso(self):
        tipos = {self.b.tipo(c) for c in self.por_id.values() if c["origem"] == "gerado"}
        gravados = {self.b.tipo(self.por_id[c["id"]]) for c in self.esperadas["casos"]
                    if self.por_id[c["id"]]["origem"] == "gerado"}
        self.assertEqual(tipos - gravados, set())
        # o REG-013 da cerveja (2203) e do refrigerante (2202), que a revisão pediu nomeado
        self.assertIn("is:sem-grupo-2203:obs", gravados)
        self.assertIn("is:sem-grupo-2202:obs", gravados)

    def test_esperadas_cobrem_todo_codigo_e_estado_da_bateria(self):
        # pede a conta local de todos os casos: só com o motor pronto e a base completa (como no robô)
        completa = base_completa_da_skill(SKILL)
        if completa is None or not (ROOT / "core" / "cd2-super" / "skills" / SKILL / "scripts" / "calc.py").exists():
            self.skipTest("precisa do calc.py da skill e da base completa (CD2_BASE_COMPLETA e CD2_BASE_COMPLETA_SKILL)")
        todos = self.b.cobertura([self.b.responder(c["requisicao"]) for c in self.b.casos(completa)])
        gravados = self.b.cobertura([c["resposta"] for c in self.esperadas["casos"]])
        for chave, valores in todos.items():
            self.assertEqual(set(valores) - set(gravados[chave]), set(), chave)

    def test_esperadas_levam_todos_os_escritos(self):
        escritos = {c["id"] for c in self.por_id.values() if c["origem"] == "escrito"}
        self.assertLessEqual(escritos, {c["id"] for c in self.esperadas["casos"]})

    def test_respostas_nao_dependem_da_maquina(self):
        # o `type` dos erros leva o host da requisição: gravado sempre de 127.0.0.1, como a conferência local
        texto = (PASTA / "esperadas.json").read_text(encoding="utf-8")
        for host in ("localhost", "host.docker.internal", "rfb-"):
            self.assertNotIn(host, texto)


class ConfigTest(unittest.TestCase):
    def test_excluidos_da_bateria_tem_motivo(self):
        config = json.loads((PASTA / "config.json").read_text(encoding="utf-8"))
        excluidos = config["excluidos_da_bateria"]
        self.assertTrue(excluidos)
        for item in excluidos:
            self.assertEqual(set(item), {"teste", "motivo"})
            self.assertTrue(item["teste"].startswith("src/test/"), item)
            self.assertTrue(item["motivo"].strip(), item)
        testes = " ".join(i["teste"] for i in excluidos)
        for parte in ("CompraGov", "testesintegracao/xml/", "testesintegracao/nfse/", "dadosabertos",
                      "pedagio", "basecalculo", "Teste_tpdoc_2_", "Teste_tpdoc_3_", "Teste_tpdoc_4_", "Teste_tpdoc_10_"):
            self.assertIn(parte, testes)


if __name__ == "__main__":
    unittest.main()
