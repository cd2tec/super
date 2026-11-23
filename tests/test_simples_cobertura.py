"""Testes da bateria da skill cbs-ibs-simples (conferencia/cbs-ibs-simples/bateria.py) e da cobertura das esperadas.

A bateria gera da base da Receita a lista de requisições que a conferência diária faz à calculadora oficial.
Estes testes não falam com a oficial: conferem a lista (determinismo, tamanho, pares), a amostra gravada em
`esperadas.json` (400 casos, cobertura, tamanho) e os caminhos que a oficial não alcança (VSN-025 e VSN-026,
com uma base de teste que tem o buraco de vigência).
"""
import copy
import importlib.util
import json
import os
import random
import shutil
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "core" / "cd2-super" / "skills" / "cbs-ibs-simples"
PASTA_CONFERENCIA = ROOT / "conferencia" / "cbs-ibs-simples"
BASE_URL_OFICIAL = "http://127.0.0.1"
AGORA = datetime(2026, 11, 22, 12, 0, 0, tzinfo=timezone.utc)
AMOSTRA = 400
LIMITE_DAS_ESPERADAS = 2_000_000


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


simples_base = load_module("simples_base", SKILL / "scripts" / "simples_base.py")
simples_motor = load_module("simples_motor", SKILL / "scripts" / "simples_motor.py")
receita_paridade = load_module("receita_paridade", ROOT / "scripts" / "receita_paridade.py")
bateria = load_module("bateria_simples", PASTA_CONFERENCIA / "bateria.py")

ESPERADAS_ARQUIVO = PASTA_CONFERENCIA / "esperadas.json"
ESPERADAS = receita_paridade.ler_json(ESPERADAS_ARQUIVO.read_text(encoding="utf-8"))
CONFIG = json.loads((PASTA_CONFERENCIA / "config.json").read_text(encoding="utf-8"))
META = json.loads((PASTA_CONFERENCIA / "meta.json").read_text(encoding="utf-8"))
IGNORAR = CONFIG["ignorar"]
EXCECOES = {e["item"]: e["motivo"] for e in CONFIG["excecoes_cobertura"]}

ITEM = {"numero": 1, "cAtivSN": "01", "cClassTrib": "000001", "baseCalculo": "1000.00", "valorImpostoSeletivo": "0.00"}
LINHA = bateria._linha("padrao", True, False, random.Random(1))


def operacao(**campos):
    corpo = {"id": "op-1", "versao": "3.0.0", "data": "2027-03-15", "tpDoc": "55", "CRT": "1",
             "aliquotasEfetivas": copy.deepcopy(LINHA), "itens": [copy.deepcopy(ITEM)]}
    corpo.update(campos)
    return corpo


class BateriaTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.casos = bateria.casos()

    def test_bateria_e_deterministica(self):
        bateria._CACHE.clear()  # a segunda lista sai de uma geração nova, não da memória
        de_novo = bateria.casos()
        self.assertEqual(de_novo, self.casos)
        self.assertEqual([c["id"] for c in de_novo], [c["id"] for c in self.casos])

    def test_outra_semente_muda_os_sorteios_e_nao_o_tamanho_dos_blocos_fixos(self):
        outra = bateria.casos(semente=1)
        self.assertNotEqual(outra, self.casos)
        fixos = lambda lista: [c for c in lista if c["grupo"] in ("consulta-erro", "erro", "item", "global")]  # noqa: E731
        self.assertEqual(fixos(outra), fixos(self.casos))

    def test_nao_usa_a_data_de_hoje_nem_sorteio_sem_semente(self):
        fonte = (PASTA_CONFERENCIA / "bateria.py").read_text(encoding="utf-8")
        for proibido in ("datetime.now", "date.today", "time.time", "random.random(", "random.choice(",
                         "random.shuffle(", "random.sample(", "random.randint(", "SystemRandom", "uuid"):
            self.assertNotIn(proibido, fonte, proibido)

    def test_tamanho_e_origens_da_bateria(self):
        self.assertGreaterEqual(len(self.casos), 3000)
        self.assertLessEqual(len(self.casos), 20000)
        origens = {c["origem"] for c in self.casos}
        self.assertEqual(origens, {"escrito", "gerado"})
        grupos = {c["grupo"] for c in self.casos}
        for grupo in ("item", "global", "erro", "consulta", "matriz", "lote", "matriz-nfse", "matriz-nomenclatura",
                      "matriz-vigencia", "matriz-compra-gov", "numerico", "ordem", "ruim-operacao", "ruim-aliquota",
                      "ruim-item", "ruim-itens", "ruim-corpo", "ruim-combinado", "consulta-matriz", "consulta-erro",
                      "consulta-versao", "compra-gov", "divergencia"):
            self.assertIn(grupo, grupos)

    def test_ids_sao_unicos_e_cada_caso_tem_requisicao(self):
        ids = [c["id"] for c in self.casos]
        self.assertEqual(len(ids), len(set(ids)))
        for caso in self.casos:
            self.assertIn(caso["requisicao"]["metodo"], ("GET", "POST"))
            self.assertTrue(caso["requisicao"]["caminho"].startswith("/"))

    def test_os_escritos_vem_primeiro_e_sao_os_do_arquivo(self):
        escritos = receita_paridade.ler_json((PASTA_CONFERENCIA / "casos-escritos.json").read_text(encoding="utf-8"))
        self.assertEqual([c["id"] for c in self.casos[:len(escritos)]], [c["id"] for c in escritos])
        self.assertEqual({c["origem"] for c in self.casos[:len(escritos)]}, {"escrito"})

    def test_a_matriz_cobre_todos_os_pares_dos_fatores_de_uma_matriz_pequena(self):
        niveis = [3, 4, 2, 5]
        linhas = bateria._pares(niveis, random.Random(7))
        vistos = {(i, linha[i], j, linha[j]) for linha in linhas for i in range(4) for j in range(i + 1, 4)}
        todos = {(i, a, j, b) for i in range(4) for j in range(i + 1, 4)
                 for a in range(niveis[i]) for b in range(niveis[j])}
        self.assertEqual(vistos, todos)
        self.assertLess(len(linhas), 3 * 4 * 2 * 5)  # pares, não o produto inteiro
        self.assertEqual(linhas, bateria._pares(niveis, random.Random(7)))

    def test_a_matriz_vem_da_base_datas_documentos_regimes_e_classificacoes(self):
        with simples_base.Base() as base:
            datas_da_transicao = [linha[0] for linha in base._con.execute(
                "SELECT TACG_INICIO_VIGENCIA FROM TRANSICAO_ALIQUOTA_COMPRA_GOV_SIMPLES_NACIONAL")]
            documentos = [str(linha[0]) for linha in base._con.execute(
                "SELECT d.TPDF_TIPO FROM TIPO_DFE_CALCULO_SIMPLES_NACIONAL c JOIN TIPO_DFE d ON d.TPDF_ID = c.TDCS_TPDF_ID")]
        corpos = [c["requisicao"]["corpo"] for c in self.casos
                  if c["origem"] == "gerado" and c["requisicao"]["metodo"] == "POST"
                  and isinstance(c["requisicao"]["corpo"], dict)]
        datas = {corpo.get("data") for corpo in corpos if isinstance(corpo.get("data"), str)}
        tipos = {corpo.get("tpDoc") for corpo in corpos if isinstance(corpo.get("tpDoc"), str)}
        for data in datas_da_transicao:
            self.assertIn(data, datas)
        self.assertLessEqual(set(documentos), tipos)
        self.assertEqual({c["requisicao"]["corpo"].get("tpEnteGov") for c in self.casos
                          if c["grupo"] == "matriz"}, {None, "1", "2", "3", "4"})
        self.assertEqual({c["requisicao"]["corpo"]["aliquotasEfetivas"]["impedimentoSublimite"] for c in self.casos
                          if c["grupo"] == "matriz"}, {False, True})
        self.assertEqual({c["requisicao"]["corpo"]["aliquotasEfetivas"]["fatorR"] for c in self.casos
                          if c["grupo"] == "matriz"}, {False, True})

    def test_lotes_respeitam_o_maximo_de_itens_do_documento(self):
        with simples_base.Base() as base:
            maximos = {str(linha[0]): linha[1] for linha in base._con.execute(
                "SELECT d.TPDF_TIPO, c.TDCS_MAXIMO_ITENS FROM TIPO_DFE_CALCULO_SIMPLES_NACIONAL c "
                "JOIN TIPO_DFE d ON d.TPDF_ID = c.TDCS_TPDF_ID")}
        lotes = [c for c in self.casos if c["grupo"] in ("lote", "numerico")]
        self.assertTrue(lotes)
        self.assertGreater(max(len(c["requisicao"]["corpo"]["itens"]) for c in lotes), 10)
        for caso in lotes:
            corpo = caso["requisicao"]["corpo"]
            maximo = maximos[corpo["tpDoc"]]
            if maximo is not None:
                self.assertLessEqual(len(corpo["itens"]), maximo, caso["id"])

    def test_cada_classificacao_com_lista_tem_um_item_aceito_e_um_recusado(self):
        casos = [c for c in self.casos if c["grupo"] == "matriz-nomenclatura"]
        por_classe = {}
        for caso in casos:
            partes = caso["id"].split("-")  # matriz-nomenclatura-00012-000001-ncm-aceito
            por_classe.setdefault(partes[3], set()).add("-".join(partes[4:]))
        with simples_base.Base() as base:
            com_ncm = [linha[0] for linha in base._con.execute(
                "SELECT DISTINCT c.CLTR_CD FROM NCM_APLICAVEL n JOIN CLASSIFICACAO_TRIBUTARIA c "
                "ON c.CLTR_ID = n.NCMA_CLTR_ID WHERE c.CLTR_INICIO_VIGENCIA <= '2027-03-15' "
                "AND (c.CLTR_FIM_VIGENCIA IS NULL OR c.CLTR_FIM_VIGENCIA >= '2027-03-15')")]
            com_nbs = [linha[0] for linha in base._con.execute(
                "SELECT DISTINCT c.CLTR_CD FROM NBS_APLICAVEL n JOIN CLASSIFICACAO_TRIBUTARIA c "
                "ON c.CLTR_ID = n.NBSA_CLTR_ID WHERE c.CLTR_INICIO_VIGENCIA <= '2027-03-15' "
                "AND (c.CLTR_FIM_VIGENCIA IS NULL OR c.CLTR_FIM_VIGENCIA >= '2027-03-15')")]
        self.assertTrue(com_ncm and com_nbs)
        for codigo in com_ncm:
            self.assertLessEqual({"ncm-aceito", "ncm-recusado"}, por_classe[codigo], codigo)
        for codigo in com_nbs:
            self.assertLessEqual({"nbs-aceito", "nbs-recusado"}, por_classe[codigo], codigo)

    def test_todo_caso_gerado_tem_resposta_local_sem_levantar_excecao(self):
        for caso in self.casos[:: max(len(self.casos) // 700, 1)]:
            with self.subTest(caso["id"]):
                resposta = bateria.responder(caso["requisicao"])
                self.assertIn("status", resposta)

    def test_lista_igual_com_a_base_completa_das_migracoes(self):
        # a base completa não mora no repositório: aponte CD2_BASE_COMPLETA para uma montada das migrações
        caminho = os.environ.get("CD2_BASE_COMPLETA")
        if not caminho or not Path(caminho).is_file():
            self.skipTest("defina CD2_BASE_COMPLETA com o caminho da base montada das migrações")
        self.assertEqual(bateria.casos(Path(caminho)), self.casos)


class AmostraTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.casos = bateria.casos()
        cls.amostra = bateria.amostra(cls.casos, AMOSTRA)

    def test_amostra_tem_exatamente_n_casos_na_ordem_da_bateria(self):
        self.assertEqual(len(self.amostra), AMOSTRA)
        posicao = {c["id"]: i for i, c in enumerate(self.casos)}
        ordem = [posicao[c["id"]] for c in self.amostra]
        self.assertEqual(ordem, sorted(ordem))
        self.assertEqual(len({c["id"] for c in self.amostra}), AMOSTRA)

    def test_amostra_e_deterministica(self):
        self.assertEqual([c["id"] for c in bateria.amostra(copy.deepcopy(self.casos), AMOSTRA)],
                         [c["id"] for c in self.amostra])

    def test_amostra_cobre_o_que_a_bateria_cobre(self):
        da_bateria = bateria.cobertura([bateria.responder(c["requisicao"]) for c in self.casos])
        da_amostra = bateria.cobertura([bateria.responder(c["requisicao"]) for c in self.amostra])
        self.assertEqual(da_amostra, da_bateria)
        for chave in ("codigos", "estados", "motivos", "regimes"):
            self.assertTrue(da_amostra[chave], chave)

    def test_amostra_leva_todos_os_escritos(self):
        escritos = [c["id"] for c in self.casos if c["origem"] == "escrito"]
        self.assertLessEqual(set(escritos), {c["id"] for c in self.amostra})

    def test_amostra_menor_que_os_escritos_e_recusada(self):
        escritos = sum(1 for c in self.casos if c["origem"] == "escrito")
        with self.assertRaises(ValueError):
            bateria.amostra(self.casos, escritos - 1)


class CoberturaTest(unittest.TestCase):
    def test_cobertura_conta_so_respostas_de_calculo(self):
        respostas = [
            {"status": 200, "corpo": {"codigo": "VSN-001", "titulo": "t", "descricao": "d"}},  # catálogo
            {"status": 200, "corpo": [{"codigo": "01"}]},  # consulta
            {"status": 404, "corpo": None},
            {"status": 422, "corpo": {"errors": [{"code": "VSN-013"}, {"code": "VSN-012"}]}},
            {"status": 200, "corpo": {"oper": {"regimeApuracao": "SN_PLENO"}, "objetos": [
                {"nObj": 1, "estadoItem": "NAO_CALCULADO", "motivoEstado": {"codigo": "CATIVSN_AUSENTE"}},
                {"nObj": 2, "estadoItem": "INCONSISTENCIA_ENTRADA", "erros": [{"code": "VSN-002"}]}]}},
        ]
        self.assertEqual(bateria.cobertura(respostas), {
            "codigos": ["VSN-002", "VSN-012", "VSN-013"], "estados": ["INCONSISTENCIA_ENTRADA", "NAO_CALCULADO"],
            "motivos": ["CATIVSN_AUSENTE"], "regimes": ["SN_PLENO"]})
        self.assertEqual(bateria.cobertura([]), {"codigos": [], "estados": [], "motivos": [], "regimes": []})

    def test_existentes_vem_do_motor_e_da_base(self):
        existentes = bateria.existentes()
        self.assertEqual(existentes["codigos"], sorted(simples_motor.CODIGOS))
        self.assertEqual(existentes["regimes"], ["PENDENTE", "PENDENTE_SUBLIMITE", "SN_PLENO", "SN_SUBLIMITE",
                                                 "SOMENTE_RECEITA"])
        self.assertEqual(len(existentes["estados"]), 5)
        self.assertEqual(len(existentes["motivos"]), 5)

    def test_cobertura_das_esperadas(self):
        vistos = bateria.cobertura([c["resposta"] for c in ESPERADAS["casos"]])
        existentes = bateria.existentes()
        for chave in ("codigos", "estados", "motivos", "regimes"):
            for item in existentes[chave]:
                with self.subTest(chave=chave, item=item):
                    self.assertTrue(item in vistos[chave] or item in EXCECOES, item)
            self.assertEqual(set(vistos[chave]) - set(existentes[chave]), set(), chave)

    def test_toda_excecao_tem_motivo_e_nao_aparece_nas_esperadas(self):
        vistos = bateria.cobertura([c["resposta"] for c in ESPERADAS["casos"]])
        todos = {item for chave in vistos for item in vistos[chave]}
        existentes = bateria.existentes()
        possiveis = {item for chave in existentes for item in existentes[chave]}
        for excecao in CONFIG["excecoes_cobertura"]:
            with self.subTest(excecao["item"]):
                self.assertEqual(set(excecao), {"item", "motivo"})
                self.assertTrue(excecao["motivo"].strip())
                self.assertIn(excecao["item"], possiveis)
                self.assertNotIn(excecao["item"], todos)
                self.assertNotIn("—", excecao["motivo"])
                self.assertNotIn("–", excecao["motivo"])

    def test_excecoes_sao_so_as_do_desenho(self):
        # As oito do desenho mais VSN-046: a oficial só a levanta no modo rígido de nomenclatura e o modo
        # não assistido valida sempre no modo permissivo (ValidacaoNomenclaturaAdapter), então nenhuma
        # entrada a provoca na 1.0.2.
        self.assertEqual(sorted(e["item"] for e in CONFIG["excecoes_cobertura"]),
                         ["ANEXO_SEM_ALIQUOTA_CBS", "FALHA_TECNICA", "VSN-000", "VSN-025", "VSN-026", "VSN-028",
                          "VSN-029", "VSN-046", "VSN-999"])


class EsperadasTest(unittest.TestCase):
    def test_esperadas_tem_400_casos_e_menos_de_2_mb(self):
        self.assertEqual(len(ESPERADAS["casos"]), AMOSTRA)
        self.assertLess(ESPERADAS_ARQUIVO.stat().st_size, LIMITE_DAS_ESPERADAS)

    def test_esperadas_sao_a_amostra_da_bateria(self):
        casos = bateria.casos()
        esperado = [c["id"] for c in bateria.amostra(casos, AMOSTRA)]
        self.assertEqual([c["id"] for c in ESPERADAS["casos"]], esperado)
        por_id = {c["id"]: c for c in casos}
        for caso in ESPERADAS["casos"]:
            self.assertEqual(caso["requisicao"], por_id[caso["id"]]["requisicao"], caso["id"])

    def test_esperadas_batem_com_a_conta_local(self):
        for caso in ESPERADAS["casos"]:
            with self.subTest(caso["id"]):
                diferencas = receita_paridade.comparar(caso["resposta"], bateria.responder(caso["requisicao"]),
                                                       IGNORAR)
                self.assertEqual(diferencas, [])

    def test_meta_das_esperadas_e_a_do_meta_json_e_a_da_base(self):
        self.assertEqual(ESPERADAS["meta"], META)
        with simples_base.Base() as base:
            self.assertEqual(base.meta(), META)

    def test_conferencia_do_meta_diz_data_casos_e_iguais(self):
        conferencia = META["conferencia"]
        self.assertRegex(conferencia["data"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertGreaterEqual(conferencia["casos"], 3000)
        self.assertEqual(conferencia["iguais"], conferencia["casos"])
        self.assertEqual(sorted(conferencia), ["casos", "data", "iguais"])

    def test_base_da_skill_mantem_o_hash_dos_dados(self):
        self.assertEqual(META["base"]["hash"],
                         "sha256:badca3b198055d8da809a761218102a5198c87f2e5895368a8b6c976223f8de9")


class ResponderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = simples_base.Base()

    @classmethod
    def tearDownClass(cls):
        cls.base.fechar()

    def test_roteia_calculo_atividades_erros_e_versao(self):
        calculo = bateria.responder({"metodo": "POST", "caminho": bateria.CAMINHO_NAO_ASSISTIDO, "corpo": operacao()})
        self.assertEqual(calculo["status"], 200)
        self.assertEqual(calculo["corpo"]["objetos"][0]["estadoItem"], "CALCULADO")
        lista = bateria.responder({"metodo": "GET", "caminho": "/calculadora/simples-nacional/atividades?data=2027-01-01"})
        self.assertEqual((lista["status"], len(lista["corpo"])), (200, 15))
        uma = bateria.responder({"metodo": "GET", "caminho": "/calculadora/simples-nacional/atividades/01?data=2027-01-01"})
        self.assertEqual(uma["corpo"]["codigo"], "01")
        erro = bateria.responder({"metodo": "GET", "caminho": bateria.CAMINHO_ERROS + "?codigo=VSN-001"})
        self.assertEqual(erro["corpo"]["codigo"], "VSN-001")
        self.assertEqual(bateria.responder({"metodo": "GET", "caminho": "/versao"})["corpo"]["versaoDb"], "V0073")
        self.assertEqual(bateria.responder({"metodo": "GET", "caminho": "/nao-existe"}), {"status": 404, "corpo": None})

    def test_o_erro_da_consulta_leva_o_instance_com_o_prefixo_da_api(self):
        resposta = bateria.responder({"metodo": "GET", "caminho": "/calculadora/simples-nacional/atividades"})
        self.assertEqual(resposta["status"], 400)
        self.assertEqual(resposta["corpo"]["instance"], "/api/calculadora/simples-nacional/atividades")
        self.assertTrue(resposta["corpo"]["type"].startswith(BASE_URL_OFICIAL))

    def test_nao_altera_a_requisicao(self):
        requisicao = {"metodo": "POST", "caminho": bateria.CAMINHO_NAO_ASSISTIDO, "corpo": operacao()}
        copia = copy.deepcopy(requisicao)
        bateria.responder(requisicao)
        self.assertEqual(requisicao, copia)


class BaseComBuracoTest(unittest.TestCase):
    """VSN-025 e VSN-026 não saem da oficial com a base real: aqui a base de teste tem o buraco de vigência."""

    def setUp(self):
        self.pasta = Path(tempfile.mkdtemp(prefix="simples_buraco_"))
        self.addCleanup(shutil.rmtree, self.pasta, ignore_errors=True)
        self.caminho = self.pasta / "base.db"
        shutil.copyfile(SKILL / "references" / "base-receita.db", self.caminho)

    def com_base(self, sql):
        con = sqlite3.connect(self.caminho)
        try:
            con.execute(sql)
            con.commit()
        finally:
            con.close()
        base = simples_base.Base(self.caminho)
        self.addCleanup(base.fechar)
        return base

    def calcular(self, base, corpo):
        return simples_motor.calcular(corpo, base, BASE_URL_OFICIAL, agora=AGORA)

    def test_vsn_025_compra_governamental_sem_transicao_vigente_na_data(self):
        base = self.com_base("DELETE FROM TRANSICAO_ALIQUOTA_COMPRA_GOV_SIMPLES_NACIONAL "
                             "WHERE TACG_INICIO_VIGENCIA = '2027-01-01'")
        status, corpo = self.calcular(base, operacao(tpEnteGov="2"))
        self.assertEqual(status, 422)
        self.assertEqual([e["code"] for e in corpo["errors"]], ["VSN-025"])
        self.assertEqual(corpo["errors"][0]["pointer"], "#/tpEnteGov")
        self.assertIn("2027-03-15", corpo["errors"][0]["detail"])
        # sem ente governamental, ou com o ente 1 (não é compra governamental), a transição não é consultada
        for ente in (None, "1"):
            status, corpo = self.calcular(base, operacao(tpEnteGov=ente))
            self.assertEqual(status, 200, ente)
        # a data com transição vigente continua calculando
        status, _ = self.calcular(base, operacao(tpEnteGov="2", data="2029-06-30"))
        self.assertEqual(status, 200)

    def test_vsn_025_na_base_real_nao_acontece(self):
        with simples_base.Base() as base:
            for data in ("2027-01-01", "2028-12-31", "2029-01-01", "2033-01-01", "2040-01-01"):
                status, _ = simples_motor.calcular(operacao(tpEnteGov="2", data=data), base, BASE_URL_OFICIAL,
                                                   agora=AGORA)
                self.assertEqual(status, 200, data)

    def test_vsn_026_tipo_de_receita_sem_parametrizacao_vigente_fica_no_item(self):
        base = self.com_base("DELETE FROM TIPO_RECEITA_BRUTA_SIMPLES_NACIONAL WHERE TRBS_CD = 1")
        status, corpo = self.calcular(base, operacao(itens=[dict(ITEM), dict(ITEM, numero=2, cClassTrib="410013")]))
        self.assertEqual(status, 200)
        primeiro, segundo = corpo["objetos"]
        self.assertEqual(primeiro["estadoItem"], "INCONSISTENCIA_ENTRADA")
        self.assertEqual([e["code"] for e in primeiro["erros"]], ["VSN-026"])
        self.assertEqual(primeiro["erros"][0]["pointer"], "#/itens/0/cClassTrib")
        self.assertIn("tpRBSN=1", primeiro["erros"][0]["detail"])
        # tpRBSN diferente de 1 segue: o item pode ter outros erros, mas não o VSN-026
        self.assertNotIn("VSN-026", [e["code"] for e in segundo.get("erros", [])])

    def test_vsn_026_na_base_real_nao_acontece(self):
        with simples_base.Base() as base:
            _, corpo = simples_motor.calcular(operacao(), base, BASE_URL_OFICIAL, agora=AGORA)
        self.assertEqual(corpo["objetos"][0]["estadoItem"], "CALCULADO")


if __name__ == "__main__":
    unittest.main()
