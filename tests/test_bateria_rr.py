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
                        self.assertFalse(item.get("nbs"))

    def test_mesma_lista_sempre(self):
        self.assertEqual(self.b.casos(), self.casos)
        self.assertNotEqual([c["requisicao"] for c in self.b.casos(semente=8)],
                            [c["requisicao"] for c in self.casos])

    def test_amostra_leva_todos_os_escritos_e_todos_os_grupos(self):
        amostra = self.b.amostra(self.casos, 400)
        self.assertEqual(len(amostra), 400)
        escritos = {c["id"] for c in self.casos if c["origem"] == "escrito"}
        self.assertLessEqual(escritos, {c["id"] for c in amostra})
        self.assertEqual({c["grupo"] for c in amostra if c["origem"] == "gerado"}, set(self.b.GRUPOS))
        self.assertEqual(self.b.amostra(self.casos, 400), amostra)

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
