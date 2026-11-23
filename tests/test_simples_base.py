"""Testes da leitura da base da Receita (simples_base.py) da skill cbs-ibs-simples.

Portados da calculadora oficial do Simples Nacional (tag 1.0.2): ParametrizacaoSimplesNacionalTest,
Teste_AtividadeSimplesService, Teste_DerivacaoAnexosSimples,
Teste_NcmNbsAplicavelService_TabelaVerdade, Teste_ValidacaoNomenclaturaService e
AtividadeSimplesControllerTest. Onde o Java usa mock de repositório, aqui usa mock.patch.object
nos métodos da Base; onde o Java usa o banco, aqui usa a base real da skill (V0073).
"""
import importlib.util
import json
import shutil
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "core" / "cd2-super" / "skills" / "cbs-ibs-simples"
ATIVIDADES = "/api/calculadora/simples-nacional/atividades"


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


sb = load_module("simples_base", SKILL / "scripts" / "simples_base.py")

DATA = date(2027, 3, 15)
DATA_PADRAO = date(2027, 1, 1)
TRIBUTOS = "CBS e IBS"


class ComBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = sb.Base()

    @classmethod
    def tearDownClass(cls):
        cls.base.fechar()


# --- Abertura da base ---------------------------------------------------------------------

class TestAbertura(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simples_base_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_versao_da_base(self):
        with sb.Base() as base:
            self.assertEqual(base.versao_base()["versao"], "V0073")

    def test_versao_da_base_traz_descricao_e_data(self):
        with sb.Base() as base:
            versao = base.versao_base()
            self.assertEqual(versao["data"], date(2026, 11, 9))
            self.assertIn("Classificações Tributárias", versao["descricao"])

    def test_meta_vem_de_cd2_meta(self):
        with sb.Base() as base:
            meta = base.meta()
            self.assertEqual(meta["skill"], "cbs-ibs-simples")
            self.assertEqual(meta["base"]["versao"], "V0073")
            meta["base"]["versao"] = "x"  # cópia: não mexe na instância
            self.assertEqual(base.meta()["base"]["versao"], "V0073")

    def test_cabecalhos_de_versao_como_na_oficial(self):
        with sb.Base() as base:
            self.assertEqual(base.cabecalhos_versao(),
                             {"X-CALC-APP-VERSION": "1.0.2", "X-CALC-DB-VERSION": "V0073"})

    def test_acha_a_base_no_layout_achatado(self):
        # Pacote do GPT: scripts e base na mesma pasta.
        shutil.copy(SKILL / "scripts" / "simples_base.py", self.tmp / "simples_base.py")
        shutil.copy(SKILL / "references" / "base-receita.db", self.tmp / "base-receita.db")
        modulo = load_module("simples_base_achatado", self.tmp / "simples_base.py")
        self.addCleanup(sys.modules.pop, "simples_base_achatado", None)
        with modulo.Base() as base:
            self.assertEqual(base.caminho.resolve(), (self.tmp / "base-receita.db").resolve())
            self.assertEqual(base.versao_base()["versao"], "V0073")

    def test_acha_a_base_no_layout_de_skill(self):
        # scripts/ ao lado de references/.
        (self.tmp / "scripts").mkdir()
        (self.tmp / "references").mkdir()
        shutil.copy(SKILL / "scripts" / "simples_base.py", self.tmp / "scripts" / "simples_base.py")
        shutil.copy(SKILL / "references" / "base-receita.db", self.tmp / "references" / "base-receita.db")
        modulo = load_module("simples_base_skill", self.tmp / "scripts" / "simples_base.py")
        self.addCleanup(sys.modules.pop, "simples_base_skill", None)
        with modulo.Base() as base:
            self.assertEqual(base.caminho.resolve(), (self.tmp / "references" / "base-receita.db").resolve())

    def test_base_ausente_levanta_base_indisponivel(self):
        shutil.copy(SKILL / "scripts" / "simples_base.py", self.tmp / "simples_base.py")
        modulo = load_module("simples_base_sem_base", self.tmp / "simples_base.py")
        self.addCleanup(sys.modules.pop, "simples_base_sem_base", None)
        with self.assertRaises(modulo.BaseIndisponivel) as erro:
            modulo.Base()
        self.assertIn("base-receita.db", str(erro.exception))
        with self.assertRaises(sb.BaseIndisponivel):
            sb.Base(self.tmp / "nao-existe.db")

    def test_base_corrompida_levanta_base_indisponivel(self):
        ruim = self.tmp / "base-receita.db"
        ruim.write_bytes(bytes(range(256)) * 40)
        with self.assertRaises(sb.BaseIndisponivel):
            sb.Base(ruim)

    def test_sqlite_valido_sem_cd2_meta_levanta_base_indisponivel(self):
        outro = self.tmp / "outro.db"
        con = sqlite3.connect(outro)
        con.execute("CREATE TABLE X (A INTEGER)")
        con.commit()
        con.close()
        with self.assertRaises(sb.BaseIndisponivel):
            sb.Base(outro)

    def test_abre_somente_leitura(self):
        with sb.Base() as base:
            with self.assertRaises(sqlite3.OperationalError):
                base._con.execute("INSERT INTO CD2_META (chave, valor) VALUES ('x', 'y')")


# --- ParametrizacaoSimplesNacionalTest ---------------------------------------------------

class TestParametrizacao(ComBase):
    def test_antes_de_2027_nenhum_documento_e_suportado(self):
        self.assertFalse(self.base.tp_doc_suportado(65, date(2026, 12, 31)))
        self.assertTrue(self.base.tp_doc_suportado(65, date(2027, 1, 1)))

    def test_tipos_de_documento_suportados_conforme_a_carga(self):
        for tp_doc in (55, 65, 91, 57, 67, 63, 93, 62):
            self.assertTrue(self.base.tp_doc_suportado(tp_doc, DATA), f"tpDoc {tp_doc}")
        self.assertFalse(self.base.tp_doc_suportado(97, DATA), "CT-e Simplificado")
        self.assertFalse(self.base.tp_doc_suportado(83, DATA), "BP-e TA")

    def test_tp_doc_sem_tratamento_na_calculadora(self):
        self.assertFalse(self.base.tp_doc_sem_tratamento(55, DATA))
        self.assertTrue(self.base.tp_doc_sem_tratamento(77, DATA), "NF-e ABI nao tem tratamento")

    def test_classificacoes_sem_tratamento_implementado(self):
        for codigo in ("550023", "620001", "620002", "620006", "620007", "800001", "800002", "810001",
                       "811001", "811003", "830001", "221002", "221004"):
            self.assertGreater(self.base.contar_tratamentos_nao_implementados(codigo, DATA), 0, codigo)
        for codigo in ("820001", "820005", "820008", "820009", "000001", "410001", "010001", "222001",
                       "820002", "820003", "820004", "820006", "820007", "999999"):
            self.assertEqual(self.base.contar_tratamentos_nao_implementados(codigo, DATA), 0, codigo)

    def test_classificacao_encerrada_em_2026_nao_conta_em_2027(self):
        for codigo in ("220001", "220002", "220003"):
            self.assertGreater(
                self.base.contar_tratamentos_nao_implementados(codigo, date(2026, 1, 1)), 0, codigo)
            self.assertEqual(self.base.contar_tratamentos_nao_implementados(codigo, DATA), 0, codigo)

    def _assert_tipo_receita(self, data, codigo, descricao, compoe, fator, pendente, exige, vedado):
        tipo = self.base.tipo_receita_bruta(codigo, data)
        self.assertIsNotNone(tipo, f"tpRBSN {codigo} em {data}")
        self.assertEqual(tipo["descricao"], descricao)
        self.assertIs(tipo["compoeReceita"], compoe)
        self.assertIs(tipo["impostoSeletivoCompoeReceita"], False)
        self.assertEqual(tipo["fatorReceita"], Decimal(fator))
        self.assertIsInstance(tipo["fatorReceita"], Decimal)
        self.assertIs(tipo["pendenteSuspenso"], pendente)
        self.assertIs(tipo["exigeAliquota"], exige)
        self.assertIs(tipo["vedadoNaoTributavel"], vedado)

    def test_tipos_de_receita_bruta_conforme_a_matriz_da_nt(self):
        for data in (DATA, date(2028, 6, 15), date(2033, 6, 15)):
            self._assert_tipo_receita(data, 0, "Não é receita bruta", False, "0", False, False, False)
            self._assert_tipo_receita(data, 1, "Receita bruta - interna", True, "1", False, True, True)
            self._assert_tipo_receita(data, 2, "Receita bruta - interna sem cálculo de IBS e CBS", True,
                                      "0", False, False, True)
            self._assert_tipo_receita(data, 3, "Receita bruta - exportação direta", True, "0", False,
                                      False, True)
            self._assert_tipo_receita(data, 4, "Receita bruta - exportação indireta", True, "1", True,
                                      True, True)
            self._assert_tipo_receita(data, 5, "Receita bruta - mercado interno e exportação", True,
                                      "0.5", False, True, True)
            self._assert_tipo_receita(data, 9, "Fornecimento incompatível com SN", True, "0", False,
                                      False, False)

    def test_tipo_receita_bruta_sem_vigencia_devolve_none(self):
        self.assertIsNone(self.base.tipo_receita_bruta(1, date(2026, 12, 31)))
        self.assertIsNone(self.base.tipo_receita_bruta(7, DATA))

    def test_matriz_de_regime_conforme_a_nt(self):
        casos = (("CRT_1", True, "DEVIDO", "PENDENTE_SUSPENSO"),
                 ("CRT_2", False, "DEVIDO", "PENDENTE_SUSPENSO"),
                 ("CRT_3_TPREGIME_2", True, "PENDENTE_SUSPENSO", "ZERO"))
        for regime, gera_ibs, devido, pendente in casos:
            linha = self.base.regime_apuracao_por_regime(regime, DATA)
            self.assertIsNotNone(linha, regime)
            self.assertIs(linha["geraIbs"], gera_ibs, regime)
            self.assertEqual(linha["destinoDevido"], devido)
            self.assertEqual(linha["destinoPendenteSuspenso"], pendente)

    def test_caracteristicas_do_calculo_por_documento(self):
        for tp_doc in (55, 65, 57, 67, 63, 93, 62):
            doc = self.base.tipo_dfe_calculo(tp_doc, DATA)
            self.assertEqual(doc["vocabularioRegime"], "CRT", tp_doc)
            self.assertIsNone(doc["casasAliquotaCalculo"])
            self.assertIsNone(doc["maximoItens"])
            self.assertIs(doc["admiteImpostoSeletivo"], True)
        nfse = self.base.tipo_dfe_calculo(91, DATA)
        self.assertEqual(nfse["vocabularioRegime"], "NFSE")
        self.assertEqual(nfse["casasAliquotaCalculo"], 2)
        self.assertEqual(nfse["maximoItens"], 1)
        self.assertIs(nfse["admiteImpostoSeletivo"], False)
        self.assertIsNone(self.base.tipo_dfe_calculo(97, DATA))

    def _crt(self, crt, tp_regime):
        return self.base.regime_apuracao("CRT", crt, tp_regime, None, None, DATA)

    def _nfse(self, op_simp_nac, reg_ap):
        return self.base.regime_apuracao("NFSE", None, None, op_simp_nac, reg_ap, DATA)

    def test_enquadramento_onze_combinacoes_levam_a_um_regime(self):
        esperados = (
            (self._crt(1, None), "SN_PLENO"), (self._crt(2, None), "SN_SUBLIMITE"),
            (self._crt(1, 1), "SOMENTE_RECEITA"), (self._crt(2, 1), "SOMENTE_RECEITA"),
            (self._crt(3, 2), "PENDENTE"),
            (self._nfse(3, 1), "SN_PLENO"), (self._nfse(3, 2), "SN_SUBLIMITE"),
            (self._nfse(3, 3), "SOMENTE_RECEITA"), (self._nfse(4, 1), "PENDENTE"),
            (self._nfse(4, 2), "PENDENTE_SUBLIMITE"), (self._nfse(4, 3), "SOMENTE_RECEITA"))
        for regime, codigo in esperados:
            self.assertIsNotNone(regime, codigo)
            self.assertEqual(regime["codigo"], codigo)

    def test_enquadramento_combinacoes_validas_sem_linha_ficam_fora_do_escopo(self):
        self.assertIsNone(self._crt(3, None))
        self.assertIsNone(self._crt(4, None))
        for op_simp_nac in (1, 2):
            for reg_ap in (1, 2, 3):
                self.assertIsNone(self._nfse(op_simp_nac, reg_ap))
        # O vocabulário separa as linhas.
        self.assertIsNone(self.base.regime_apuracao("CRT", None, None, 3, 1, DATA))

    def test_regimes_de_apuracao_comportamento_por_codigo_neutro(self):
        pendente_sublimite = self._nfse(4, 2)
        self.assertEqual((pendente_sublimite["codigo"], pendente_sublimite["calculaTributos"],
                          pendente_sublimite["geraIbs"], pendente_sublimite["destinoDevido"],
                          pendente_sublimite["destinoPendenteSuspenso"]),
                         ("PENDENTE_SUBLIMITE", True, False, "PENDENTE_SUSPENSO", "ZERO"))
        somente_receita = self._crt(1, 1)
        self.assertIs(somente_receita["calculaTributos"], False)
        self.assertIsNone(somente_receita["geraIbs"])
        self.assertIsNone(somente_receita["destinoDevido"])
        self.assertIsNone(somente_receita["destinoPendenteSuspenso"])
        self.assertEqual([self._crt(1, None)["id"], self._crt(2, None)["id"], self._crt(3, 2)["id"]],
                         [1, 2, 3])
        self.assertEqual(self._crt(2, None)["id"], self._nfse(3, 2)["id"])

    def test_enquadramento_traz_a_linha_do_regime_junto(self):
        linhas = self.base.enquadramentos_vigentes("CRT", 1, None, None, None, DATA)
        self.assertEqual(len(linhas), 1)
        self.assertEqual(linhas[0]["crt"], 1)
        self.assertEqual(linhas[0]["regime"]["codigo"], "SN_PLENO")
        self.assertEqual(linhas[0]["inicioVigencia"], date(2027, 1, 1))

    def test_tipos_de_receita_bruta_linhas_da_nfse(self):
        for codigo in (0, 1, 2, 3, 4, 5, 9):
            padrao = self.base.tipo_receita_bruta(codigo, DATA)
            nfse = self.base.tipo_receita_bruta(codigo, DATA, "NFSE")
            self.assertIsNone(padrao["vocabularioRegime"])
            self.assertEqual(nfse["vocabularioRegime"], "NFSE", codigo)
            self.assertEqual(nfse["descricao"], padrao["descricao"])
            self.assertEqual(nfse["compoeReceita"], padrao["compoeReceita"])
            self.assertEqual(nfse["vedadoNaoTributavel"], padrao["vedadoNaoTributavel"])
            self.assertIs(nfse["impostoSeletivoCompoeReceita"], False)
            self.assertIs(nfse["pendenteSuspenso"], False)
            self.assertEqual(nfse["fatorReceita"], Decimal("1" if codigo == 1 else "0"))
            self.assertIs(nfse["exigeAliquota"], codigo == 1)
        # Vocabulário CRT não tem linhas próprias: vale a padrão.
        self.assertIsNone(self.base.tipo_receita_bruta(4, DATA, "CRT")["vocabularioRegime"])

    def test_catalogo_de_atividades_indicadores_de_iss(self):
        com_iss = {"07", "09", "10", "12", "13", "14"}
        com_beneficio = {"12", "13"}
        for atividade in self.base.atividades_vigentes(DATA):
            codigo = atividade["cAtivSN"]
            self.assertIs(atividade["issNoSimples"], codigo in com_iss, codigo)
            self.assertIs(atividade["admiteBeneficioMunicipal"], codigo in com_beneficio, codigo)

    def test_situacoes_do_iss_conforme_a_secao_6(self):
        situacoes = self.base.situacoes_iss_par_aliquota(DATA)
        self.assertEqual(len(situacoes), 10)
        por_regra = {}
        for s in situacoes:
            por_regra.setdefault(s["regra"], []).append(f"{s['campo']}={s['valor']}")
        self.assertEqual(por_regra["ISS_FORA_DO_SIMPLES"],
                         ["CRT=2", "regApIBSCBSSN=2", "regApTribSN=2", "regApTribSN=3"])
        self.assertEqual(por_regra["ISS_NAO_INCIDE"], ["tribISSQN=2", "tribISSQN=3", "tribISSQN=4"])
        self.assertEqual(por_regra["ISENCAO_MUNICIPAL"], ["tpBM=1"])
        self.assertEqual(por_regra["ISS_RETIDO"], ["tpRetISSQN=2", "tpRetISSQN=3"])
        regras = ["DOCUMENTO", "ATIVIDADE_NAO_TRIBUTADA", "ANEXO_SEM_ISS", "ISS_FORA_DO_SIMPLES",
                  "ATIVIDADE_SEM_ISS", "ISS_NAO_INCIDE", "ISENCAO_MUNICIPAL", "ISS_RETIDO", "PADRAO"]
        for s in situacoes:
            self.assertEqual(s["tipoAliquota"], "SEM_REDISTRIBUICAO")
            self.assertEqual(s["ordem"], regras.index(s["regra"]) + 1)

    def test_transicao_compra_gov_vigencias_contiguas_e_sem_sobreposicao(self):
        linhas = self.base._linhas(
            sb._TRANSICAO, f"SELECT {sb._colunas(sb._TRANSICAO, 't')} "
                           "FROM TRANSICAO_ALIQUOTA_COMPRA_GOV_SIMPLES_NACIONAL t")
        self.assertEqual(len(linhas), 6)
        # Uma data por mês cobre as viradas; o dia a dia sai caro em Python sem mudar o resultado.
        for ano in range(2027, 2036):
            for mes in range(1, 13):
                for dia in (1, 28):
                    ref = date(ano, mes, dia)
                    vigentes = [t for t in linhas if t["inicioVigencia"] <= ref
                                and (t["fimVigencia"] is None or ref <= t["fimVigencia"])]
                    self.assertEqual(len(vigentes), 1, ref)

    def test_transicao_compra_gov_data_anterior_a_toda_vigencia_devolve_none(self):
        self.assertIsNone(self.base.fator_transicao_compra_gov(date(2025, 12, 31)))

    def test_transicao_compra_gov_fatores_conforme_a_nt(self):
        for esperado, data in (("0.00", date(2027, 1, 1)), ("0.00", date(2028, 12, 31)),
                               ("0.10", date(2029, 6, 1)), ("0.20", date(2030, 6, 1)),
                               ("0.30", date(2031, 6, 1)), ("0.40", date(2032, 6, 1)),
                               ("1.00", date(2033, 1, 1)), ("1.00", date(2099, 12, 31))):
            fator = self.base.fator_transicao_compra_gov(data)
            self.assertEqual(fator, Decimal(esperado), data)
            self.assertIsInstance(fator, Decimal)

    def test_catalogo_de_atividades_quinze_linhas_com_codigos_oficiais(self):
        atividades = self.base.atividades_vigentes(DATA)
        self.assertEqual(len(atividades), 15)
        por_ordem = {a["ordem"]: a for a in atividades}
        for ordem in range(1, 15):
            self.assertEqual(por_ordem[ordem]["cAtivSN"], f"{ordem:02d}")
            self.assertIs(por_ordem[ordem]["naoTributavel"], False)
            if ordem != 9:
                self.assertIsNone(por_ordem[ordem]["anexoComFatorR"], "so a 09 tem Fator R")
        nao_tributavel = por_ordem[15]
        self.assertEqual(nao_tributavel["cAtivSN"], "90")
        self.assertIs(nao_tributavel["naoTributavel"], True)
        self.assertIsNone(nao_tributavel["anexoSemFatorR"])
        self.assertIsNone(nao_tributavel["anexoComFatorR"])
        self.assertEqual((por_ordem[9]["anexoSemFatorR"], por_ordem[9]["anexoComFatorR"]), ("V", "III"))
        self.assertIn("7.02 e 7.05", por_ordem[12]["descricao"])
        self.assertIn("7.02 e 7.05", por_ordem[13]["descricao"])
        self.assertIn("ferroviário", por_ordem[10]["descricao"])
        self.assertIn("16.01", por_ordem[10]["descricao"])

    def test_atividade_por_c_ativ_sn_e_admissibilidade_no_calculo(self):
        atividade = self.base.atividades_por_c_ativ_sn("01", DATA)[0]
        self.assertEqual(atividade["ordem"], 1)
        self.assertEqual(self.base.atividades_por_c_ativ_sn("77", DATA), [])
        self.assertEqual(self.base.atividades_por_c_ativ_sn("01", date(2026, 12, 31)), [])
        self.assertEqual(self.base.contar_vinculos_atividade_tp_doc(atividade["id"], 55, DATA), 1)
        self.assertEqual(self.base.contar_vinculos_atividade_tp_doc(atividade["id"], 91, DATA), 0)

    def test_associativa_de_atividades_vinculos_vigentes_conforme_a_carga(self):
        por_id = {a["id"]: a["cAtivSN"] for a in self.base.atividades_vigentes(DATA)}
        vinculos = self.base.vinculos_tp_doc_vigentes(DATA)
        self.assertEqual(len(vinculos), 29)
        por_codigo = {}
        for v in vinculos:
            por_codigo.setdefault(por_id[v["atividadeId"]], []).append(v["tpDoc"])
        por_codigo = {k: sorted(v) for k, v in por_codigo.items()}
        for codigo in ("01", "02", "03", "04"):
            self.assertEqual(por_codigo[codigo], [55, 65], codigo)
        for codigo in ("07", "08", "09", "10", "11", "12", "13", "14"):
            self.assertEqual(por_codigo[codigo], [91], codigo)
        self.assertEqual(por_codigo["05"], [57, 63, 67, 93])
        self.assertEqual(por_codigo["06"], [62])
        self.assertEqual(por_codigo["90"], [55, 57, 62, 63, 65, 67, 91, 93])

    def test_classificacao_resolve_so_o_grupo_da_cbs(self):
        # 000001 existe na CBS (tpRBSN 1) e no Imposto Seletivo (tpRBSN 0).
        vigentes = self.base.classificacao("000001", DATA)
        self.assertEqual(len(vigentes), 1)
        self.assertEqual(vigentes[0]["tpRBSN"], 1)
        self.assertEqual(vigentes[0]["codigo"], "000001")
        self.assertEqual(self.base.classificacao("999999", DATA), [])
        self.assertEqual(self.base.classificacao("000001", date(2025, 12, 31)), [])

    def test_classificacao_vinculada_a_tipo_de_documento(self):
        self.assertGreater(self.base.contar_vinculos_classificacao_tp_doc("000001", 55, DATA), 0)
        self.assertEqual(self.base.contar_vinculos_classificacao_tp_doc("999999", 55, DATA), 0)

    def test_sigla_e_ncm_zero_do_tipo_de_documento(self):
        doc = self.base.tipo_dfe_calculo(55, DATA)
        self.assertEqual(self.base.sigla_tipo_dfe_vigente(doc["tipoDfeId"], DATA), "NF-e")
        self.assertIs(self.base.permite_ncm_zero(doc["tipoDfeId"], DATA), True)
        self.assertIsNone(self.base.sigla_tipo_dfe_vigente(9999, DATA))
        self.assertIsNone(self.base.permite_ncm_zero(9999, DATA))

    def test_data_com_hora_vale_so_pela_data(self):
        self.assertTrue(self.base.tp_doc_suportado(65, datetime(2027, 1, 1, 23, 59)))
        with self.assertRaises(TypeError):
            self.base.tp_doc_suportado(65, "2027-01-01")

    def test_consulta_repetida_usa_o_dicionario_da_instancia(self):
        with sb.Base() as base:
            base.tp_doc_suportado(55, DATA)
            antes = len(base._cache)
            base.tp_doc_suportado(55, DATA)
            self.assertEqual(len(base._cache), antes)
            linhas = base.atividades_vigentes(DATA)
            linhas[0]["descricao"] = "mexi"
            self.assertNotEqual(base.atividades_vigentes(DATA)[0]["descricao"], "mexi")


# --- Teste_DerivacaoAnexosSimples ---------------------------------------------------------

class TestDerivacaoAnexos(unittest.TestCase):
    def _d(self, sem, com, anexos):
        return sb.derivar_anexos(sem, com, anexos)

    def test_atividade_9(self):
        casos = (
            ({"I", "II"}, False, ["III", "V"], "INDISPONIVEL"),
            ({"I", "V"}, True, ["III"], "INDISPONIVEL"),
            ({"I", "III"}, True, ["V"], "OBRIGATORIO"),
            ({"III", "V"}, True, [], "OPCIONAL"),
            (None, True, [], "OPCIONAL"),
            (set(), False, ["III", "V"], "INDISPONIVEL"),
        )
        for anexos, disponivel, faltantes, modo in casos:
            d = self._d("V", "III", anexos)
            self.assertEqual((d["disponivel"], d["anexosFaltantes"], d["modoFatorR"]),
                             (disponivel, faltantes, modo), anexos)

    def test_atividade_2(self):
        casos = (({"I", "II"}, True, []), ({"II", "III"}, False, ["I"]), (None, True, []))
        for anexos, disponivel, faltantes in casos:
            d = self._d("I", None, anexos)
            self.assertEqual((d["disponivel"], d["anexosFaltantes"], d["modoFatorR"]),
                             (disponivel, faltantes, "NAO_APLICAVEL"), anexos)

    def test_atividade_sem_anexo_esta_sempre_disponivel(self):
        for anexos in (None, set(), {"I", "V"}):
            d = self._d(None, None, anexos)
            self.assertEqual((d["disponivel"], d["anexosFaltantes"], d["modoFatorR"]),
                             (True, [], "NAO_APLICAVEL"))

    def test_invariante_so_com_fator_r_levanta(self):
        with self.assertRaises(RuntimeError):
            self._d(None, "III", None)

    def test_faltantes_saem_em_ordem_de_romano(self):
        self.assertEqual(self._d("V", "III", {"IV"})["anexosFaltantes"], ["III", "V"])


# --- Teste_AtividadeSimplesService e AtividadeSimplesControllerTest ---------------------------

class TestAtividades(ComBase):
    def _lista(self, data=DATA_PADRAO, **filtros):
        status, corpo = self.base.atividades(data, **filtros)
        self.assertEqual(status, 200, corpo)
        return corpo

    def test_quinze_atividades_na_tabela(self):
        status, corpo = self.base.atividades(DATA)
        self.assertEqual(status, 200)
        self.assertEqual(len(corpo), 15)
        # Forma do item, como a oficial devolve (chaves na ordem do JsonPropertyOrder; sem os
        # campos opcionais quando os filtros não foram informados).
        self.assertEqual(list(corpo[0]), [
            "codigo", "ordem", "descricao", "anexoSemFatorR", "anexoComFatorR", "sujeitaFatorR",
            "rotuloAnexo", "rotuloCurtoAnexo", "disponivel", "anexosFaltantes", "modoFatorR",
            "tpDocsAdmissiveis"])
        self.assertEqual(corpo[0], {
            "codigo": "01", "ordem": 1,
            "descricao": "Revenda de mercadorias, venda de mercadorias industrializadas pelo contribuinte",
            "anexoSemFatorR": "I", "anexoComFatorR": None, "sujeitaFatorR": False, "rotuloAnexo": "I",
            "rotuloCurtoAnexo": "I", "disponivel": True, "anexosFaltantes": [],
            "modoFatorR": "NAO_APLICAVEL", "tpDocsAdmissiveis": ["55", "65"]})
        self.assertEqual([a["codigo"] for a in corpo],
                         [f"{n:02d}" for n in range(1, 15)] + ["90"])
        # O corpo se serializa em JSON sem adaptador.
        self.assertEqual(json.loads(json.dumps(corpo)), corpo)

    def test_lista_as_quinze_atividades_sem_filtro(self):
        corpo = self._lista()
        self.assertEqual(len(corpo), 15)
        self.assertEqual(corpo[1]["rotuloAnexo"], "I, menos ICMS")
        self.assertEqual(corpo[1]["rotuloCurtoAnexo"], "I")
        self.assertEqual((corpo[8]["codigo"], corpo[8]["modoFatorR"], corpo[8]["disponivel"]),
                         ("09", "OPCIONAL", True))
        self.assertEqual((corpo[13]["codigo"], corpo[13]["ordem"]), ("14", 14))
        self.assertEqual((corpo[14]["codigo"], corpo[14]["anexoSemFatorR"], corpo[14]["anexoComFatorR"],
                          corpo[14]["sujeitaFatorR"], corpo[14]["modoFatorR"]),
                         ("90", None, None, False, "NAO_APLICAVEL"))

    def test_filtro_de_anexos_i_e_v(self):
        corpo = self._lista(anexo="I,V")
        self.assertEqual((corpo[8]["disponivel"], corpo[8]["anexosFaltantes"], corpo[8]["modoFatorR"]),
                         (True, ["III"], "INDISPONIVEL"))
        self.assertEqual((corpo[4]["codigo"], corpo[4]["disponivel"], corpo[4]["anexosFaltantes"]),
                         ("05", False, ["III"]))

    def test_parametro_anexos_repetido_e_lista(self):
        for anexo in (["III", "V"], ["III,V"], "III,V", ["III", "", "V"]):
            corpo = self._lista(anexo=anexo)
            self.assertEqual((corpo[8]["anexosFaltantes"], corpo[8]["modoFatorR"]), ([], "OPCIONAL"))

    def test_fator_r_obrigatorio_quando_so_o_anexo_iii_esta_configurado(self):
        corpo = self._lista(anexo="III")
        self.assertEqual((corpo[8]["anexosFaltantes"], corpo[8]["modoFatorR"]), (["V"], "OBRIGATORIO"))

    def test_anexos_vazio_e_nenhum_configurado(self):
        for anexo in ("", []):
            corpo = self._lista(anexo=anexo)
            self.assertEqual(len(corpo), 15)
            self.assertEqual((corpo[8]["disponivel"], corpo[8]["anexosFaltantes"]), (False, ["III", "V"]))
            self.assertEqual((corpo[14]["disponivel"], corpo[14]["modoFatorR"]), (True, "NAO_APLICAVEL"))

    def test_sem_tp_doc_traz_a_lista_de_documentos_mas_nao_o_campo_do_documento(self):
        corpo = self._lista()
        self.assertEqual(corpo[0]["tpDocsAdmissiveis"], ["55", "65"])
        self.assertEqual(corpo[4]["tpDocsAdmissiveis"], ["57", "63", "67", "93"])
        self.assertEqual(corpo[5]["tpDocsAdmissiveis"], ["62"])
        self.assertEqual(corpo[8]["tpDocsAdmissiveis"], ["91"])
        self.assertEqual(corpo[14]["tpDocsAdmissiveis"],
                         ["55", "57", "62", "63", "65", "67", "91", "93"])
        for atividade in corpo:
            self.assertNotIn("admissivelNoDocumento", atividade)
            self.assertNotIn("compativelComClassTrib", atividade)

    def test_tp_doc_55_marca_a_admissibilidade_pela_whitelist(self):
        corpo = self._lista(tp_doc="55")
        por_codigo = {a["codigo"]: a for a in corpo}
        for codigo in ("01", "02", "03", "04", "90"):
            self.assertIs(por_codigo[codigo]["admissivelNoDocumento"], True, codigo)
            self.assertIs(por_codigo[codigo]["disponivel"], True, codigo)
        for codigo in ("05", "06", "07", "09", "13"):
            self.assertIs(por_codigo[codigo]["admissivelNoDocumento"], False, codigo)
            self.assertIs(por_codigo[codigo]["disponivel"], False, codigo)
            self.assertEqual(por_codigo[codigo]["anexosFaltantes"], [], codigo)

    def test_tp_doc_91_nfse(self):
        por_codigo = {a["codigo"]: a for a in self._lista(tp_doc="91")}
        for codigo in ("01", "02", "03", "04", "05"):
            self.assertIs(por_codigo[codigo]["admissivelNoDocumento"], False, codigo)
        for codigo in ("07", "12", "13", "90"):
            self.assertIs(por_codigo[codigo]["admissivelNoDocumento"], True, codigo)
            self.assertIs(por_codigo[codigo]["disponivel"], True, codigo)

    def test_tp_doc_valido_fora_da_whitelist_do_calculo_e_aceito(self):
        # 97 (CT-e Simplificado) existe na TIPO_DFE: nenhuma atividade o admite.
        corpo = self._lista(tp_doc="97")
        self.assertEqual(len(corpo), 15)
        for atividade in corpo:
            self.assertIs(atividade["admissivelNoDocumento"], False)
            self.assertIs(atividade["disponivel"], False)

    def test_dois_motivos_simultaneos_inadmissivel_e_anexo_faltante(self):
        corpo = self._lista(anexo="II", tp_doc="91")
        self.assertEqual((corpo[0]["admissivelNoDocumento"], corpo[0]["anexosFaltantes"],
                          corpo[0]["disponivel"]), (False, ["I"], False))
        self.assertIs(corpo[12]["admissivelNoDocumento"], True)

    def test_tp_doc_em_branco_equivale_a_ausente(self):
        for tp_doc in ("", "  ", None):
            self.assertNotIn("admissivelNoDocumento", self._lista(tp_doc=tp_doc)[0])

    def test_tp_doc_canonizado_antes_da_comparacao(self):
        # Verificado na oficial: "055", "+55" e dígitos de outras escritas valem como 55.
        for tp_doc in ("055", "+55", " 55 ", 55, "٥٥"):
            self.assertIs(self._lista(tp_doc=tp_doc)[0]["admissivelNoDocumento"], True, tp_doc)

    def test_c_class_trib_que_declara_receita_marca_so_o_nao_tributavel(self):
        corpo = self._lista(c_class_trib="000001")
        self.assertEqual(len(corpo), 15)
        for atividade in corpo[:14]:
            self.assertIs(atividade["compativelComClassTrib"], True, atividade["codigo"])
            self.assertIs(atividade["disponivel"], True, atividade["codigo"])
        self.assertEqual((corpo[14]["codigo"], corpo[14]["compativelComClassTrib"],
                          corpo[14]["disponivel"], corpo[14]["anexosFaltantes"]), ("90", False, False, []))

    def test_c_class_trib_de_tp_rbsn_0_e_9_nao_marca_atividade_alguma(self):
        for c_class_trib in ("410001", "010001"):
            for atividade in self._lista(c_class_trib=c_class_trib):
                self.assertIs(atividade["compativelComClassTrib"], True, c_class_trib)
                self.assertIs(atividade["disponivel"], True)

    def test_motivos_de_documento_e_de_classificacao_combinados(self):
        corpo = self._lista(tp_doc="97", c_class_trib="000001")
        self.assertEqual((corpo[14]["admissivelNoDocumento"], corpo[14]["compativelComClassTrib"],
                          corpo[14]["disponivel"]), (False, False, False))
        self.assertEqual((corpo[0]["admissivelNoDocumento"], corpo[0]["compativelComClassTrib"],
                          corpo[0]["disponivel"]), (False, True, False))

    def test_c_class_trib_com_espacos_e_resolvido_pela_linha_da_cbs(self):
        corpo = self._lista(DATA, c_class_trib=" 000001 ")
        self.assertIs(corpo[14]["compativelComClassTrib"], False)

    def test_data_anterior_a_toda_vigencia_lista_vazia(self):
        self.assertEqual(self.base.atividades(date(2025, 12, 31)), (200, []))

    def test_busca_por_codigo(self):
        status, corpo = self.base.atividade("09", DATA_PADRAO)
        self.assertEqual(status, 200)
        self.assertEqual((corpo["codigo"], corpo["ordem"], corpo["anexoSemFatorR"],
                          corpo["anexoComFatorR"], corpo["sujeitaFatorR"]), ("09", 9, "V", "III", True))
        self.assertEqual(corpo["descricao"], 'Prestação de serviços \u2014 Sujeitos ao Fator "R"')
        self.assertEqual(corpo, self._lista()[8])

    def test_busca_por_codigo_recebe_o_mesmo_tratamento_dos_filtros(self):
        _, corpo = self.base.atividade("01", DATA_PADRAO, tp_doc="91")
        self.assertEqual((corpo["admissivelNoDocumento"], corpo["disponivel"], corpo["tpDocsAdmissiveis"]),
                         (False, False, ["55", "65"]))
        _, corpo = self.base.atividade("01", DATA_PADRAO, tp_doc="55")
        self.assertEqual((corpo["admissivelNoDocumento"], corpo["disponivel"]), (True, True))
        _, corpo = self.base.atividade("90", DATA_PADRAO, c_class_trib="000001")
        self.assertEqual((corpo["compativelComClassTrib"], corpo["disponivel"]), (False, False))

    def test_codigo_inexistente_e_404_sem_corpo(self):
        self.assertEqual(self.base.atividade("77", DATA_PADRAO), (404, None))
        self.assertEqual(self.base.atividade("1", DATA_PADRAO), (404, None))
        self.assertEqual(self.base.atividade("09", date(2025, 12, 31)), (404, None))

    def test_busca_por_codigo_valida_os_filtros_mesmo_sem_achar_o_codigo(self):
        status, corpo = self.base.atividade("77", DATA_PADRAO, tp_doc="abc")
        self.assertEqual(status, 400)
        self.assertEqual(corpo["instance"], f"{ATIVIDADES}/77")

    def test_busca_por_codigo_ignora_anexos(self):
        # O GET /{codigo} não tem o parâmetro anexos.
        self.assertEqual(self.base.atividade("09", DATA_PADRAO)[0], 200)
        with self.assertRaises(TypeError):
            self.base.atividade("09", DATA_PADRAO, anexo="X")


class TestErrosDeAtividades(ComBase):
    """Corpos de erro do ApiExceptionHandler, como a oficial 1.0.2 devolve (conferidos nela)."""

    AGORA = datetime(2027, 3, 15, 12, 0, 0, 816642, tzinfo=timezone.utc)

    def test_data_mal_formada(self):
        for valor in ("01/01/2027", "2027-1-1", "2027-02-30", "2027-01-01T00:00", "x"):
            status, corpo = self.base.atividades(valor, agora=self.AGORA)
            self.assertEqual(status, 400, valor)
            detalhe = f"Parâmetro inválido [data: {valor}] "
            self.assertEqual(corpo, {
                "type": "http://127.0.0.1/errors/tipo-argumento-invalido",
                "title": "Tipo de argumento inválido", "status": 400, "detail": detalhe,
                "instance": ATIVIDADES, "timestamp": "2027-03-15T12:00:00.816642Z",
                "errors": [{"title": "Tipo de argumento inválido", "pointer": "#/data",
                            "detail": detalhe.strip()}]})
            self.assertEqual(list(corpo), ["type", "title", "status", "detail", "instance", "timestamp",
                                           "errors"])

    def test_data_ausente_ou_vazia(self):
        for valor in (None, ""):
            status, corpo = self.base.atividades(valor, agora=self.AGORA, base_url="https://x.gov.br")
            self.assertEqual(status, 400)
            detalhe = "O parâmetro obrigatório 'data' (LocalDate) não foi informado."
            self.assertEqual(corpo, {
                "type": "https://x.gov.br/errors/parametro-obrigatorio-ausente",
                "title": "Parâmetro obrigatório ausente", "status": 400, "detail": detalhe,
                "instance": ATIVIDADES, "timestamp": "2027-03-15T12:00:00.816642Z",
                "errors": [{"title": "Parâmetro obrigatório ausente", "pointer": "#/data",
                            "detail": detalhe}]})

    def test_data_com_deslocamento_vale_pela_data(self):
        for valor in ("2027-01-01+01:00", "2027-01-01Z"):
            status, corpo = self.base.atividades(valor)
            self.assertEqual((status, len(corpo)), (200, 15), valor)

    def test_anexo_fora_do_dominio(self):
        for anexo in ("I,X", "i", ["X"]):
            status, corpo = self.base.atividades(DATA_PADRAO, anexo=anexo)
            self.assertEqual(status, 400, anexo)
            self.assertEqual(list(corpo), ["type", "title", "status", "detail", "instance"])
            self.assertEqual(corpo["type"], "about:blank")
            self.assertEqual(corpo["title"], "Bad Request")
            self.assertEqual(corpo["status"], 400)
            self.assertEqual(corpo["instance"], ATIVIDADES)
        _, corpo = self.base.atividades(DATA_PADRAO, anexo="X")
        self.assertEqual(corpo["detail"], "Anexo inválido: 'X'. Valores aceitos: I, II, III, IV, V.")

    def test_tp_doc_mal_formado(self):
        for tp_doc in ("abc", "5_5", "99999999999", "5.5"):
            status, corpo = self.base.atividades(DATA_PADRAO, tp_doc=tp_doc)
            self.assertEqual(status, 400, tp_doc)
            self.assertEqual(corpo, {
                "type": "about:blank", "title": "Bad Request", "status": 400,
                "detail": f"Tipo de documento fiscal inválido: '{tp_doc}'. Informe o valor numérico da "
                          "coluna TPDF_TIPO da TIPO_DFE (ex.: 55, 65, 91).",
                "instance": ATIVIDADES})

    def test_tp_doc_sem_linha_na_tipo_dfe(self):
        for tp_doc in ("99", "-1"):
            status, corpo = self.base.atividades(DATA_PADRAO, tp_doc=tp_doc)
            self.assertEqual(status, 400)
            self.assertEqual(corpo["detail"], f"Tipo de documento fiscal desconhecido: '{tp_doc}'. "
                                              "Não há linha na TIPO_DFE com TPDF_TIPO correspondente.")

    def test_c_class_trib_inexistente_ou_fora_de_vigencia(self):
        for data, codigo in ((DATA_PADRAO, "999999"), (date(2025, 12, 31), "000001")):
            status, corpo = self.base.atividades(data, c_class_trib=codigo)
            self.assertEqual(status, 400)
            self.assertEqual(corpo["detail"],
                             f"Código de classificação tributária desconhecido ou fora de vigência: "
                             f"'{codigo}'. Informe um cClassTrib do grupo da CBS vigente na data de "
                             "referência.")

    def test_c_class_trib_sem_efeito_vigente_do_tp_rbsn(self):
        with mock.patch.object(self.base, "tipo_receita_bruta", return_value=None):
            status, corpo = self.base.atividades(DATA_PADRAO, c_class_trib="000001")
        self.assertEqual(status, 400)
        self.assertEqual(corpo["detail"],
                         "Não há parametrização de efeito vigente para o tipo de receita bruta "
                         "tpRBSN=1 do cClassTrib '000001' na data de referência.")

    def test_ordem_das_validacoes(self):
        # data antes de anexos, anexos antes de tpDoc, tpDoc antes de cClassTrib.
        self.assertEqual(self.base.atividades("x", anexo="X", tp_doc="abc")[1]["errors"][0]["pointer"],
                         "#/data")
        self.assertIn("Anexo inválido",
                      self.base.atividades(DATA_PADRAO, anexo="X", tp_doc="abc")[1]["detail"])
        self.assertIn("Tipo de documento",
                      self.base.atividades(DATA_PADRAO, tp_doc="abc", c_class_trib="999999")[1]["detail"])

    def test_instancia_base_url_e_instante_vem_do_pedido(self):
        _, corpo = self.base.atividades("x", instancia="/api/y", base_url="http://h", agora=self.AGORA)
        self.assertEqual((corpo["instance"], corpo["type"]), ("/api/y", "http://h/errors/tipo-argumento-invalido"))
        _, corpo = self.base.atividade("0 9", "x")
        self.assertEqual(corpo["instance"], f"{ATIVIDADES}/0%209")

    def test_instante_no_formato_iso_instant(self):
        casos = ((datetime(2027, 3, 15, 12, 0, 0, tzinfo=timezone.utc), "2027-03-15T12:00:00Z"),
                 (datetime(2027, 3, 15, 12, 0, 0, 123000, tzinfo=timezone.utc), "2027-03-15T12:00:00.123Z"),
                 (datetime(2027, 3, 15, 12, 0, 0, 123456, tzinfo=timezone.utc), "2027-03-15T12:00:00.123456Z"))
        for agora, esperado in casos:
            self.assertEqual(sb._instante(agora), esperado)
        self.assertRegex(sb._instante(None), r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{3}(\d{3})?)?Z$")


# --- Nomenclatura (domain/service) --------------------------------------------------------

class TestTabelaVerdade(ComBase):
    """Teste_NcmNbsAplicavelService_TabelaVerdade."""

    CASOS = ((False, False, False), (False, True, False), (True, False, False), (True, False, True),
             (True, True, False), (True, True, True))
    ID, CODIGO, DATA = 9, "200044", date(2026, 1, 1)

    def _rodar(self, tipo, valor, excecao, criticar):
        for a, e, s in self.CASOS:
            with mock.patch.object(self.base, f"tem_{tipo}_aplicavel_para_classificacao", return_value=True), \
                    mock.patch.object(self.base, f"tem_{tipo}_aplicavel", return_value=a), \
                    mock.patch.object(self.base, f"tem_excecao_{tipo}_aplicavel", return_value=e), \
                    mock.patch.object(self.base, f"tem_{tipo}_aplicavel_sem_excecao", return_value=s):
                rejeita = (not a) or (e and not s)
                chamada = lambda: getattr(self.base, criticar)(valor, self.ID, self.CODIGO, self.DATA, TRIBUTOS)
                if rejeita:
                    with self.assertRaises(excecao, msg=f"A/E/S={a}/{e}/{s}"):
                        chamada()
                else:
                    chamada()

    def test_tabela_verdade_ncm(self):
        self._rodar("ncm", "12345678", sb.NcmNaoVinculadaException, "criticar_ncm_contra_anexo")

    def test_tabela_verdade_nbs(self):
        self._rodar("nbs", "123456789", sb.NbsNaoVinculadaException, "criticar_nbs_contra_anexo")

    def test_codigo_incompleto_com_anexo_falha_sem_consultar_vinculo_especifico(self):
        for tipo, valor, excecao in (("ncm", "123", sb.NcmCompletoNaoInformadoException),
                                     ("nbs", "123", sb.NbsCompletoNaoInformadoException)):
            with mock.patch.object(self.base, f"tem_{tipo}_aplicavel_para_classificacao", return_value=True), \
                    mock.patch.object(self.base, f"tem_{tipo}_aplicavel") as especifico:
                with self.assertRaises(excecao):
                    getattr(self.base, f"validar_{tipo}")(valor, self.ID, self.CODIGO, self.DATA, TRIBUTOS)
                especifico.assert_not_called()

    def test_sem_anexo_aceita_codigo_completo_sem_critica_e_incompleto(self):
        for tipo, completo in (("ncm", "12345678"), ("nbs", "123456789")):
            with mock.patch.object(self.base, f"tem_{tipo}_aplicavel_para_classificacao", return_value=False), \
                    mock.patch.object(self.base, f"tem_{tipo}_aplicavel") as especifico, \
                    mock.patch.object(self.base, f"tem_excecao_{tipo}_aplicavel") as excecao:
                validar = getattr(self.base, f"validar_{tipo}")
                self.assertIs(validar(completo, self.ID, self.CODIGO, self.DATA, TRIBUTOS), True)
                self.assertIs(validar("123", self.ID, self.CODIGO, self.DATA, TRIBUTOS), True)
                especifico.assert_not_called()
                excecao.assert_not_called()

    def test_mensagens_das_excecoes_como_no_java(self):
        self.assertEqual(str(sb.NcmNaoVinculadaException("1", "2", "CBS e IBS")),
                         "NCM de código 1 não vinculada à Classificação Tributária de código 2 (CBS e IBS)")
        self.assertEqual(str(sb.NbsNaoVinculadaException("1", "2", "CBS e IBS")),
                         "NBS de código 1 não vinculada à Classificação Tributária de código 2 (CBS e IBS)")
        self.assertEqual(str(sb.NcmCompletoNaoInformadoException()), "NCM completo não informado")
        self.assertEqual(str(sb.NbsCompletoNaoInformadoException()), "NBS completo não informado")
        self.assertEqual(str(sb.NcmNbsSimultaneasException()),
                         "Não é permitido informar NCM e NBS simultaneamente para um item")
        self.assertEqual(str(sb.NomenclaturaIncompativelTipoDfeException("NCM", "1", "NFe")),
                         "NCM não permitida para a Classificação Tributária de código 1 no tipo de "
                         "documento NFe")
        self.assertEqual(str(sb.NomenclaturaNaoPermitidaTipoDfeException("1", "CTe")),
                         "NCM/NBS não permitido para a Classificação Tributária de código 1 no tipo de "
                         "documento CTe")
        self.assertEqual(str(sb.NomenclaturaObrigatoriaNaoInformadaException("1", "NFSe")),
                         "A Classificação Tributária de código 1 exige NCM ou NBS completo para o tipo "
                         "de documento NFSe")
        self.assertEqual(str(sb.SiglaDFeNaoEncontradaException("x")), "Sigla DFe inválida: x")
        self.assertEqual(str(sb.NcmNaoEncontradaException("1", date(2027, 1, 1))),
                         "NCM de código 1 não encontrada para a data 2027-01-01")
        self.assertEqual(str(sb.NbsNaoEncontradaException("1", date(2027, 1, 1))),
                         "NBS de código 1 não encontrada para a data 2027-01-01")


class TestValidacaoNomenclatura(ComBase):
    """Teste_ValidacaoNomenclaturaService: determinar_nomenclatura e as críticas são simuladas."""

    ID, CODIGO, DATA = 7, "200044", date(2026, 1, 1)

    def _validar(self, resultado, sigla, ncm, nbs, modo=sb.MODO_PERMISSIVO):
        with mock.patch.object(self.base, "determinar_nomenclatura", return_value=resultado):
            self.base.validar_nomenclatura(sigla, self.ID, self.CODIGO, ncm, nbs, self.DATA, TRIBUTOS, modo)

    def setUp(self):
        patchers = [mock.patch.object(self.base, n) for n in
                    ("criticar_ncm_contra_anexo", "criticar_nbs_contra_anexo", "validar_ncm", "validar_nbs")]
        self.ncm, self.nbs, self.validar_ncm, self.validar_nbs = [p.start() for p in patchers]
        for p in patchers:
            self.addCleanup(p.stop)

    def test_ncm_obrigatorio_e_valida_contra_anexo(self):
        self._validar("NCM", "NFCom", "87100000", None)
        self.ncm.assert_called_once_with("87100000", self.ID, self.CODIGO, self.DATA, TRIBUTOS)
        self.nbs.assert_not_called()

    def test_nomenclatura_ncm_rejeita_ausencia_ou_nbs_incompativel(self):
        with self.assertRaises(sb.NcmCompletoNaoInformadoException):
            self._validar("NCM", "NFCom", None, None)
        with self.assertRaises(sb.NomenclaturaIncompativelTipoDfeException):
            self._validar("NCM", "NFCom", "87100000", "115012000")

    def test_nbs_obrigatorio_e_rejeita_ncm_ou_codigo_incompleto(self):
        with self.assertRaises(sb.NbsCompletoNaoInformadoException):
            self._validar("NBS", "NFCom", None, None)
        with self.assertRaises(sb.NomenclaturaIncompativelTipoDfeException):
            self._validar("NBS", "NFCom", "87100000", "115012000")
        with self.assertRaises(sb.NbsCompletoNaoInformadoException):
            self._validar("NBS", "NFCom", None, "123")

    def test_excecoes_seguem_o_ramo_de_ncm_e_de_nbs(self):
        self._validar("EXCECAO_NCM", "NFe", "87100000", None)
        self._validar("EXCECAO_NBS", "NFSe", None, "115012000")
        self.ncm.assert_called_once()
        self.nbs.assert_called_once()

    def test_misto_aceita_uma_das_nomenclaturas_e_exige_uma_quando_ambas_vazias(self):
        self._validar("MISTO", "NFCom", "87100000", None)
        self._validar("MISTO", "NFCom", None, "115012000")
        with self.assertRaises(sb.NomenclaturaObrigatoriaNaoInformadaException):
            self._validar("MISTO", "NFCom", "", " ")
        with self.assertRaises(sb.NcmCompletoNaoInformadoException):
            self._validar("MISTO", "NFCom", "123", None)
        self.ncm.assert_called_once_with("87100000", self.ID, self.CODIGO, self.DATA, TRIBUTOS)
        self.nbs.assert_called_once_with("115012000", self.ID, self.CODIGO, self.DATA, TRIBUTOS)

    def test_sem_no_trio_aceita_ausencia_e_natural_mas_rejeita_contraria(self):
        self._validar("SEM", "NFe", None, None, sb.MODO_RIGIDO)
        self._validar("SEM", "NFe", "87100000", None, sb.MODO_RIGIDO)
        with self.assertRaises(sb.NomenclaturaIncompativelTipoDfeException):
            self._validar("SEM", "NFe", None, "115012000")
        self._validar("SEM", "NFSe", None, "115012000", sb.MODO_RIGIDO)
        with self.assertRaises(sb.NomenclaturaIncompativelTipoDfeException):
            self._validar("SEM", "NFSe", "87100000", None)

    def test_sem_fora_do_trio_distingue_modo_rigido_e_permissivo(self):
        with self.assertRaises(sb.NomenclaturaNaoPermitidaTipoDfeException):
            self._validar("SEM", "NFCom", "87100000", None, sb.MODO_RIGIDO)
        self._validar("SEM", "NFCom", "87100000", None, sb.MODO_PERMISSIVO)
        self.validar_ncm.assert_called_once_with("87100000", self.ID, self.CODIGO, self.DATA, TRIBUTOS)
        self._validar("SEM", "NFCom", None, "115012000", sb.MODO_PERMISSIVO)
        self.validar_nbs.assert_called_once_with("115012000", self.ID, self.CODIGO, self.DATA, TRIBUTOS)

    def test_sigla_invalida(self):
        with self.assertRaises(sb.SiglaDFeNaoEncontradaException):
            self._validar("SEM", "XYZ", None, None)

    def test_sigla_e_normalizada_como_no_java(self):
        for sigla in ("NF-e", "nfe", "NFe", "N F e", "NF_E"):
            self.assertEqual(sb.sigla_dfe_por_normalizada(sigla), "NFE", sigla)
        self.assertEqual(sb.sigla_dfe_por_normalizada("CT-e Simplificado"), "CTESIMPLIFICADO")


class TestNomenclaturaNaBaseReal(ComBase):
    """As mesmas regras contra a V0073, sem simulação."""

    def test_existe_ncm_e_nbs_pela_vigencia(self):
        self.assertTrue(self.base.existe_ncm("10062010", DATA))
        self.assertFalse(self.base.existe_ncm("99999999", DATA))
        self.assertTrue(self.base.existe_nbs("122012000", DATA))
        self.assertFalse(self.base.existe_nbs("1", DATA))
        self.assertFalse(self.base.existe_nbs("122012000", date(2020, 1, 1)))

    def test_classificacao_cbs_ibs(self):
        classificacao = self.base.classificacao_cbs_ibs("200003", DATA)
        self.assertEqual((classificacao["id"], classificacao["codigo"], classificacao["nomenclatura"]),
                         (14, "200003", "NCM"))
        with self.assertRaises(sb.ClassificacaoTributariaNaoEncontradaException) as erro:
            self.base.classificacao_cbs_ibs("999999", DATA)
        self.assertEqual(str(erro.exception),
                         "Classificação tributária não encontrada para código 999999 e data "
                         "2027-03-15 (CBS e IBS)")

    def test_determinar_nomenclatura(self):
        esperado = {
            ("200003", "NFe"): "NCM", ("200003", "NFSe"): "SEM", ("200003", "CTe"): "SEM",
            ("200044", "NFSe"): "MISTO", ("200044", "NFe"): "SEM",
            ("200038", "NFe"): "EXCECAO_NCM", ("200038", "NFCe"): "EXCECAO_NCM",
            ("200038", "NFSe"): "EXCECAO_NBS", ("200038", "NFCom"): "SEM",
            ("000001", "NFe"): "SEM"}
        for (codigo, sigla), resultado in esperado.items():
            self.assertEqual(self.base.determinar_nomenclatura(sigla, codigo, DATA), resultado,
                             (codigo, sigla))
        with self.assertRaises(sb.SiglaDFeNaoEncontradaException):
            self.base.determinar_nomenclatura("XYZ", "200003", DATA)

    def _validar(self, sigla, id_classificacao, codigo, ncm, nbs, modo=sb.MODO_PERMISSIVO):
        self.base.validar_nomenclatura(sigla, id_classificacao, codigo, ncm, nbs, DATA, TRIBUTOS, modo)

    def test_validacao_completa_nfe_com_classificacao_de_ncm(self):
        self._validar("NFe", 14, "200003", "10062010", None)
        casos = (("02074300", None, sb.NcmNaoVinculadaException),  # NCM com exceção vigente
                 ("01012100", None, sb.NcmNaoVinculadaException),  # fora do anexo
                 (None, None, sb.NcmCompletoNaoInformadoException),
                 ("1006", None, sb.NcmCompletoNaoInformadoException),
                 ("10062010", "123456789", sb.NomenclaturaIncompativelTipoDfeException))
        for ncm, nbs, erro in casos:
            with self.assertRaises(erro, msg=(ncm, nbs)):
                self._validar("NFe", 14, "200003", ncm, nbs)

    def test_validacao_completa_nfse_misto(self):
        with self.assertRaises(sb.NomenclaturaObrigatoriaNaoInformadaException):
            self._validar("NFSe", 55, "200044", None, None)
        with self.assertRaises(sb.NbsNaoVinculadaException):
            self._validar("NFSe", 55, "200044", None, "122012000")

    def test_validacao_fora_do_trio_por_modo(self):
        with self.assertRaises(sb.NomenclaturaNaoPermitidaTipoDfeException):
            self._validar("CTe", 14, "200003", "10062010", None, sb.MODO_RIGIDO)
        self._validar("NFCom", 14, "200003", "10062010", None, sb.MODO_PERMISSIVO)

    def test_validar_ncm_e_nbs_aplicavel(self):
        self.assertIs(self.base.validar_ncm("123", 1, "000001", DATA, TRIBUTOS), True)
        self.assertIs(self.base.validar_ncm("12345678", 1, "000001", DATA, TRIBUTOS), True)
        with self.assertRaises(sb.NcmCompletoNaoInformadoException):
            self.base.validar_ncm("123", 14, "200003", DATA, TRIBUTOS)
        self.assertIs(self.base.validar_nbs("123", 1, "000001", DATA, TRIBUTOS), True)
        with self.assertRaises(sb.NbsCompletoNaoInformadoException):
            self.base.validar_nbs("123", 39, "200028", DATA, TRIBUTOS)

    def test_predicados_de_vinculo_do_ncm(self):
        # 02074300 é exceção da linha genérica 0207 da classificação 14, e não há outra linha livre.
        self.assertTrue(self.base.tem_ncm_aplicavel("02074300", 14, DATA))
        self.assertTrue(self.base.tem_excecao_ncm_aplicavel("02074300", 14, DATA))
        self.assertFalse(self.base.tem_ncm_aplicavel_sem_excecao("02074300", 14, DATA))
        self.assertFalse(self.base.tem_ncm_aplicavel("02074300", 14, date(2025, 1, 1)))
        self.assertTrue(self.base.tem_ncm_aplicavel_para_classificacao(14, DATA))
        self.assertFalse(self.base.tem_ncm_aplicavel_para_classificacao(1, DATA))

    def test_tipo_dfe_da_classificacao_traz_a_sigla(self):
        tipos = self.base.tipo_dfe_classificacao(14, DATA)
        siglas = {sb.normalizar_sigla(t["tipoDfe"]["sigla"]) for t in tipos}
        self.assertIn("NFE", siglas)
        self.assertEqual(self.base.tipo_dfe_classificacao(14, date(2025, 1, 1)), [])


if __name__ == "__main__":
    unittest.main()
