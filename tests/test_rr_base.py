"""Testes da base da Receita que a skill cbs-ibs-regime-regular leva dentro do pacote e da leitura dela
(scripts/rr_base.py).

As amostras saem da própria base (o primeiro NCM com Imposto Seletivo ad valorem, o primeiro município
do RS), para os testes seguirem valendo quando o robô trocar as tabelas. Cada método de rr_base.py é
comparado com uma consulta escrita à mão aqui, ou com a regra refeita em Python.
"""
import importlib.util
import json
import shutil
import sqlite3
import sys
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import receita_base  # noqa: E402

PASTA = ROOT / "conferencia" / "cbs-ibs-regime-regular"
CONFIG = json.loads((PASTA / "config.json").read_text(encoding="utf-8"))
META = json.loads((PASTA / "meta.json").read_text(encoding="utf-8"))
BASE = ROOT / CONFIG["base_destino"]
SKILL = ROOT / "core" / "cd2-super" / "skills" / "cbs-ibs-regime-regular"


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


rb = load_module("rr_base", SKILL / "scripts" / "rr_base.py")


class TestBaseDoRegimeRegular(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(f"file:{BASE}?mode=ro", uri=True)
        self.addCleanup(self.con.close)

    def test_base_bate_com_o_meta(self):
        self.assertEqual(receita_base.ler_meta(BASE), META)
        self.assertEqual(
            receita_base.hash_conteudo(self.con, CONFIG["tabelas"], CONFIG["hash_ignorar_colunas"]),
            META["base"]["hash"])
        self.assertRegex(META["base"]["versao"], r"^V\d{4}$")

    def test_so_tabelas_do_config_e_cd2_meta(self):
        nomes = {r[0] for r in self.con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        self.assertEqual(nomes, set(CONFIG["tabelas"]) | {"CD2_META"})

    def test_transferencia_cbs_tem_linha(self):
        # Sem linha nesta tabela o motor do regime regular devolve CAL-999.
        n = self.con.execute("SELECT COUNT(*) FROM TRANSFERENCIA_CBS_ENTE_GOV").fetchone()[0]
        self.assertGreater(n, 0)

    def test_base_menor_que_5_mb(self):
        self.assertLess(BASE.stat().st_size, 5 * 1024 * 1024)



def _dec(valor):
    """O BigDecimal que o sqlite-jdbc lê de uma coluna REAL: o texto do número."""
    if valor is None:
        return None
    return Decimal(repr(valor)) if isinstance(valor, float) else Decimal(str(valor))


def _vigente(inicio, fim, data: str) -> bool:
    """`:data BETWEEN inicio AND COALESCE(fim, :data)`, comparando texto como o SQLite."""
    return inicio <= data <= (fim if fim is not None else data)


class TestLeituraDaBase(unittest.TestCase):
    """rr_base.Base contra consultas escritas à mão."""

    DATA_IS = date(2027, 6, 1)

    @classmethod
    def setUpClass(cls):
        cls.base = rb.Base(BASE)
        cls.con = sqlite3.connect(f"file:{BASE}?mode=ro", uri=True)

    @classmethod
    def tearDownClass(cls):
        cls.base.fechar()
        cls.con.close()

    def um(self, sql, *args):
        return self.con.execute(sql, args).fetchone()

    # --- infraestrutura -----------------------------------------------------------------

    def test_meta_e_versao(self):
        self.assertEqual(self.base.meta(), META)
        versao, = self.um("SELECT VRBD_VERSAO_BASE_DADO FROM VERSAO_BASE_DADO ORDER BY VRBD_ID DESC")
        self.assertEqual(self.base.versao_base()["versao"], versao)
        self.assertEqual(self.base.cabecalhos_versao(), {
            "X-CALC-APP-VERSION": META["calculadora"]["versao"], "X-CALC-DB-VERSION": versao})

    def test_base_ausente(self):
        with self.assertRaises(rb.BaseIndisponivel):
            rb.Base(BASE.parent / "nao-existe.db")

    def test_exception_classificacoes(self):
        self.assertEqual(rb.EXCEPTION_CLASSIFICACOES, {"200038", "515001"})

    # --- município e UF -----------------------------------------------------------------

    def test_buscar_uf_por_municipio_primeiro_de_rs(self):
        municipio, = self.um(
            "SELECT m.MUNI_CD FROM MUNICIPIO m JOIN UF u ON u.UF_CD = m.MUNI_UF_CD "
            "WHERE u.UF_SIGLA = 'RS' ORDER BY m.MUNI_CD")
        self.assertEqual(self.base.buscar_uf_por_municipio(municipio), "RS")
        self.assertIsNone(self.base.buscar_uf_por_municipio(1))

    def test_validar_municipio(self):
        municipio, = self.um(
            "SELECT m.MUNI_CD FROM MUNICIPIO m JOIN UF u ON u.UF_CD = m.MUNI_UF_CD "
            "WHERE u.UF_SIGLA = 'RS' ORDER BY m.MUNI_CD")
        self.assertEqual(self.base.validar_municipio(municipio, "RS"), 0)
        self.assertEqual(self.base.validar_municipio(municipio, "rs"), 0)
        self.assertEqual(self.base.validar_municipio(municipio, "SC"), 2)
        self.assertEqual(self.base.validar_municipio(1, "RS"), 1)

    def test_uf(self):
        codigo, = self.um("SELECT UF_CD FROM UF WHERE UF_SIGLA = 'RS'")
        self.assertEqual(self.base.consultar_por_sigla("rs"), codigo)
        self.assertIsNone(self.base.consultar_por_sigla("XX"))
        self.assertIsNone(self.base.consultar_por_sigla(None))
        self.assertTrue(self.base.existe_uf("Rs"))
        self.assertFalse(self.base.existe_uf("XX"))

    # --- tipo de documento --------------------------------------------------------------

    def test_buscar_por_tipo(self):
        data = date(2027, 1, 1)
        for tipo in (55, 65):
            esperado = self.um(
                "SELECT TPDF_ID, TPDF_SIGLA, TPDF_B_POSSUI_TRATAMENTO_CALCULADORA FROM TIPO_DFE "
                "WHERE TPDF_TIPO = ? AND ? BETWEEN TPDF_INICIO_VIGENCIA AND COALESCE(TPDF_FIM_VIGENCIA, ?)",
                tipo, data.isoformat(), data.isoformat())
            achado = self.base.buscar_por_tipo(tipo, data)
            self.assertEqual((achado["id"], achado["sigla"], achado["possuiTratamento"]),
                             (esperado[0], esperado[1], bool(esperado[2])))
            self.assertEqual(achado["tipo"], tipo)
        self.assertIsNone(self.base.buscar_por_tipo(1, data))
        self.assertIsNone(self.base.buscar_por_tipo(55, date(2000, 1, 1)))

    def test_buscar_tipo_dfe_classificacao(self):
        data = date(2027, 1, 1)
        id_classificacao, = self.um("SELECT CLTR_ID FROM CLASSIFICACAO_TRIBUTARIA WHERE CLTR_CD = '515001'")
        esperado = sorted(r[0] for r in self.con.execute(
            "SELECT td.TPDF_SIGLA FROM TIPO_DFE_CLASSIFICACAO t JOIN TIPO_DFE td ON td.TPDF_ID = t.TDCL_TPDF_ID "
            "WHERE t.TDCL_CLTR_ID = ? AND t.TDCL_INICIO_VIGENCIA <= ? "
            "AND (t.TDCL_FIM_VIGENCIA IS NULL OR t.TDCL_FIM_VIGENCIA >= ?)",
            (id_classificacao, data.isoformat(), data.isoformat())))
        achado = self.base.buscar_tipo_dfe_classificacao(id_classificacao, data)
        self.assertEqual(sorted(t["tipoDfe"]["sigla"] for t in achado), esperado)
        self.assertTrue(esperado)

    # --- situação e classificação tributária --------------------------------------------

    def _classificacao_cbs_ibs(self):
        return self.um(
            "SELECT ct.CLTR_ID, ct.CLTR_CD, st.SITR_CD FROM CLASSIFICACAO_TRIBUTARIA ct "
            "JOIN SITUACAO_TRIBUTARIA st ON st.SITR_ID = ct.CLTR_SITR_ID "
            "JOIN TRIBUTO_SITUACAO_TRIBUTARIA tst ON tst.TRST_SITR_ID = st.SITR_ID AND tst.TRST_TBTO_ID = 2 "
            "WHERE ct.CLTR_CD = '000001' AND ct.CLTR_INICIO_VIGENCIA <= '2027-01-01' "
            "AND (ct.CLTR_FIM_VIGENCIA IS NULL OR ct.CLTR_FIM_VIGENCIA >= '2027-01-01')")

    def test_situacao_tributaria(self):
        data = date(2027, 1, 1)
        _, _, cst = self._classificacao_cbs_ibs()
        self.assertTrue(self.base.existe_cst(cst, 2, data))
        self.assertFalse(self.base.existe_cst("999", 2, data))
        st = self.base.consultar_situacao_tributaria_por_codigo(cst, 2, data)
        gibscbs, = self.um("SELECT SITR_IND_GIBSCBS FROM SITUACAO_TRIBUTARIA WHERE SITR_CD = ?", cst)
        self.assertEqual(st["codigo"], cst)
        self.assertEqual(st["inGrupoIbsCbs"], None if gibscbs is None else bool(gibscbs))
        self.assertIsNone(self.base.consultar_situacao_tributaria_por_codigo("999", 2, data))

    def test_classificacao_tributaria(self):
        data = date(2027, 1, 1)
        id_classificacao, codigo, cst = self._classificacao_cbs_ibs()
        achado = self.base.buscar_classificacao_tributaria(codigo, 2, data)
        self.assertEqual((achado["id"], achado["codigo"], achado["cst"]), (id_classificacao, codigo, cst))
        self.assertIsNone(self.base.buscar_classificacao_tributaria("999999", 2, data))
        calculo = self.base.buscar_classificacao_tributaria_calculo(id_classificacao)
        tipo, gred = self.um(
            "SELECT ct.CLTR_TIPO_ALIQUOTA, st.SITR_IND_GRED FROM CLASSIFICACAO_TRIBUTARIA ct "
            "JOIN SITUACAO_TRIBUTARIA st ON st.SITR_ID = ct.CLTR_SITR_ID WHERE ct.CLTR_ID = ?", id_classificacao)
        self.assertEqual((calculo["codigo"], calculo["tipoAliquota"], calculo["inGrupoReducao"]),
                         (codigo, tipo, None if gred is None else bool(gred)))
        self.assertIsNone(self.base.buscar_classificacao_tributaria_calculo(-1))

    def test_tratamento(self):
        data = date(2027, 1, 1)
        id_classificacao, _, _ = self._classificacao_cbs_ibs()
        id_tratamento, = self.um(
            "SELECT TRCL_TRTR_ID FROM TRATAMENTO_CLASSIFICACAO WHERE TRCL_CLTR_ID = ? "
            "AND TRCL_INICIO_VIGENCIA <= ? AND (TRCL_FIM_VIGENCIA IS NULL OR TRCL_FIM_VIGENCIA >= ?)",
            id_classificacao, data.isoformat(), data.isoformat())
        tc = self.base.buscar_tratamento_classificacao(id_classificacao, data)
        self.assertEqual((tc["idTratamentoTributario"], tc["idClassificacaoTributaria"]),
                         (id_tratamento, id_classificacao))
        tt = self.base.buscar_tratamento_tributario(id_tratamento)
        expressao, = self.um("SELECT TRTR_EXPRESSAO_ALIQUOTA FROM TRATAMENTO_TRIBUTARIO WHERE TRTR_ID = ?",
                             id_tratamento)
        self.assertEqual((tt["id"], tt["expressaoAliquota"]), (id_tratamento, expressao))
        self.assertIsInstance(tt["inPossuiPercentualReducao"], bool)
        self.assertIsNone(self.base.buscar_tratamento_tributario(-1))

    def test_fundamentacao_classificacao(self):
        data = date(2027, 1, 1)
        id_classificacao, _, _ = self._classificacao_cbs_ibs()
        esperado = self.um(
            "SELECT fl.FDLG_TEXTO_CURTO, ct.CLTR_MEMORIA_CALCULO FROM FUNDAMENTACAO_CLASSIFICACAO fc "
            "JOIN FUNDAMENTACAO_LEGAL fl ON fl.FDLG_ID = fc.FDCL_FDLG_ID "
            "JOIN CLASSIFICACAO_TRIBUTARIA ct ON ct.CLTR_ID = fc.FDCL_CLTR_ID "
            "WHERE fc.FDCL_CLTR_ID = ? AND ? BETWEEN fc.FDCL_INICIO_VIGENCIA AND COALESCE(fc.FDCL_FIM_VIGENCIA, ?)",
            id_classificacao, data.isoformat(), data.isoformat())
        achado = self.base.buscar_fundamentacao_classificacao(id_classificacao, data)
        self.assertEqual((achado["textoCurto"], achado["memoriaCalculo"]), esperado)

    def test_percentual_reducao(self):
        id_classificacao, tributo, inicio, valor = self.um(
            "SELECT PERE_CLTR_ID, PERE_TBTO_ID, PERE_INICIO_VIGENCIA, PERE_VALOR FROM PERCENTUAL_REDUCAO "
            "WHERE PERE_VALOR > 0 ORDER BY PERE_ID")
        data = date.fromisoformat(inicio)
        self.assertEqual(self.base.buscar_percentual_reducao(id_classificacao, tributo, data), _dec(valor))
        self.assertIsNone(self.base.buscar_percentual_reducao(-1, tributo, data))

    # --- alíquotas ----------------------------------------------------------------------

    def test_aliquota_referencia(self):
        tributo, inicio, valor = self.um(
            "SELECT ALRE_TBTO_ID, ALRE_INICIO_VIGENCIA, ALRE_VALOR FROM ALIQUOTA_REFERENCIA ORDER BY ALRE_ID")
        data = date.fromisoformat(inicio)
        self.assertEqual(self.base.buscar_aliquota_referencia(tributo, data), _dec(valor))
        self.assertIsNone(self.base.buscar_aliquota_referencia(tributo, date(2000, 1, 1)))
        # AliquotaPadraoRepository.buscarAliquota: a referência sempre vem, a padrão só se houver linha
        padrao = self.base.buscar_aliquota(tributo, None, None, data)
        tem_padrao = self.um(
            "SELECT COUNT(*) FROM ALIQUOTA_PADRAO p JOIN ALIQUOTA_REFERENCIA r ON r.ALRE_ID = p.ALPA_ALRE_ID "
            "WHERE r.ALRE_TBTO_ID = ? AND p.ALPA_MUNI_CD IS NULL AND p.ALPA_UF_CD IS NULL", tributo)[0]
        self.assertEqual(padrao["valorReferencia"], _dec(valor))
        if not tem_padrao:
            self.assertEqual((padrao["valorPadrao"], padrao["formaAplicacao"]), (None, None))
        self.assertIsNone(self.base.buscar_aliquota(tributo, None, None, date(2000, 1, 1)))

    def test_aliquota_ad_valorem_por_classificacao(self):
        tributo, id_classificacao, inicio, valor = self.um(
            "SELECT AADV_TBTO_ID, AADV_CLTR_ID, AADV_INICIO_VIGENCIA, AADV_VALOR FROM ALIQUOTA_AD_VALOREM "
            "WHERE AADV_CLTR_ID IS NOT NULL ORDER BY AADV_ID")
        data = date.fromisoformat(inicio)
        self.assertEqual(
            self.base.buscar_aliquota_ad_valorem_por_classificacao_tributaria(tributo, id_classificacao, data),
            _dec(valor))
        # sem NBS a consulta da alíquota de serviço não acha nada (EXISTS em NBS com código nulo)
        self.assertIsNone(self.base.buscar_aliquota_ad_valorem_dto(None, tributo, id_classificacao, data))

    def _produtos(self, tabela, sigla, taxa, sigla_taxa, excecao, sigla_excecao, tributo, data):
        """Linhas vigentes do produto e das exceções, para refazer a regra em Python."""
        d = data.isoformat()
        produtos = [r for r in self.con.execute(
            f"SELECT a.{sigla}_ID, a.{sigla}_NCM_CD, a.{sigla}_INICIO_VIGENCIA, a.{sigla}_FIM_VIGENCIA, "
            f"v.{sigla_taxa}_VALOR FROM {tabela} a JOIN {taxa} v ON v.{sigla_taxa}_ID = a.{sigla}_{sigla_taxa}_ID "
            f"WHERE v.{sigla_taxa}_TBTO_ID = ?", (tributo,)) if _vigente(r[2], r[3], d)]
        excecoes = [r for r in self.con.execute(
            f"SELECT {sigla_excecao}_{sigla}_ID, {sigla_excecao}_NCM_CD, {sigla_excecao}_INICIO_VIGENCIA, "
            f"{sigla_excecao}_FIM_VIGENCIA FROM {excecao}") if _vigente(r[2], r[3], d)]
        return produtos, excecoes

    @staticmethod
    def _regra(ncm, produtos, excecoes, existe_ncm):
        """A regra das consultas de alíquota de produto: prefixo, sem exceção que cubra o NCM ou o prefixo, o
        prefixo mais longo primeiro."""
        if not existe_ncm:
            return []
        achados = []
        for id_produto, prefixo, _, _, valor in produtos:
            if not ncm.startswith(prefixo):
                continue
            cobre = [e for e in excecoes if e[0] == id_produto and (ncm.startswith(e[1]) or prefixo.startswith(e[1]))]
            if not cobre:
                achados.append((len(prefixo), valor))
        return sorted(achados, key=lambda a: -a[0])

    def test_aliquota_ad_valorem_produto_primeiro_ncm_com_is(self):
        produtos, excecoes = self._produtos(
            "ALIQUOTA_AD_VALOREM_PRODUTO", "AAVP", "ALIQUOTA_AD_VALOREM", "AADV", "EXCECAO_AD_VALOREM_PRODUTO",
            "EAVP", 1, self.DATA_IS)
        ncms = [r[0] for r in self.con.execute(
            "SELECT NCM_CD FROM NCM WHERE LENGTH(NCM_CD) = 8 ORDER BY NCM_CD")]
        com_is = [n for n in ncms if any(n.startswith(p[1]) for p in produtos)]
        self.assertTrue(com_is, "a base não tem NCM com Imposto Seletivo ad valorem")
        primeiro = com_is[0]
        esperado = self._regra(primeiro, produtos, excecoes, True)
        self.assertTrue(esperado)
        achado = self.base.buscar_aliquota_ad_valorem(primeiro, 1, self.DATA_IS)
        self.assertEqual(achado, {"valor": _dec(esperado[0][1])})
        self.assertEqual(self.base.aliquota_ad_valorem_produto(primeiro, self.DATA_IS), _dec(esperado[0][1]))
        self.assertTrue(self.base.existe_ncm_ad_valorem(primeiro, 1, self.DATA_IS))
        # NCM que não está na tabela NCM: o EXISTS corta tudo
        self.assertIsNone(self.base.buscar_aliquota_ad_valorem(primeiro[:6], 1, self.DATA_IS))
        self.assertFalse(self.base.existe_ncm_ad_valorem(primeiro[:6], 1, self.DATA_IS))
        # fora da vigência
        self.assertIsNone(self.base.buscar_aliquota_ad_valorem(primeiro, 1, date(2026, 1, 1)))

    def test_aliquota_ad_valorem_produto_amostra_com_excecoes(self):
        produtos, excecoes = self._produtos(
            "ALIQUOTA_AD_VALOREM_PRODUTO", "AAVP", "ALIQUOTA_AD_VALOREM", "AADV", "EXCECAO_AD_VALOREM_PRODUTO",
            "EAVP", 1, self.DATA_IS)
        ncms = {r[0] for r in self.con.execute("SELECT NCM_CD FROM NCM")}
        amostra = sorted({n for n in ncms if len(n) == 8 and any(n.startswith(p[1]) for p in produtos)})
        amostra += sorted({n for n in ncms if len(n) == 8 and any(n.startswith(e[1]) for e in excecoes)})
        for ncm in amostra[:200]:
            with self.subTest(ncm=ncm):
                esperado = self._regra(ncm, produtos, excecoes, ncm in ncms)
                achado = self.base.buscar_aliquota_ad_valorem(ncm, 1, self.DATA_IS)
                self.assertEqual(achado, {"valor": _dec(esperado[0][1])} if esperado else None)
                self.assertEqual(self.base.existe_ncm_ad_valorem(ncm, 1, self.DATA_IS), bool(esperado))

    def test_aliquota_ad_valorem_produto_prefixo_mais_longo_e_excecoes(self):
        # A base de hoje não tem exceção que cubra um NCM com alíquota: numa cópia, dois produtos (prefixo de 4
        # e de 6 dígitos) para um NCM sem alíquota, e depois as exceções que os tiram.
        ncms = [r[0] for r in self.con.execute("SELECT NCM_CD FROM NCM WHERE LENGTH(NCM_CD) = 8 ORDER BY NCM_CD")]
        produtos, _ = self._produtos(
            "ALIQUOTA_AD_VALOREM_PRODUTO", "AAVP", "ALIQUOTA_AD_VALOREM", "AADV", "EXCECAO_AD_VALOREM_PRODUTO",
            "EAVP", 1, self.DATA_IS)
        ncm = next(n for n in ncms if not any(n[:2] == p[1][:2] for p in produtos))
        pasta = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, pasta)
        copia = pasta / "base.db"
        shutil.copyfile(BASE, copia)

        def alterar(*comandos):
            con = sqlite3.connect(copia)
            with con:
                for comando in comandos:
                    con.execute(comando)
            con.close()
            with rb.Base(copia) as base:
                return (base.buscar_aliquota_ad_valorem(ncm, 1, self.DATA_IS),
                        base.existe_ncm_ad_valorem(ncm, 1, self.DATA_IS))

        achado = alterar(
            "INSERT INTO ALIQUOTA_AD_VALOREM VALUES (9001, 12.5, 1, '2027-01-01', NULL, NULL)",
            "INSERT INTO ALIQUOTA_AD_VALOREM VALUES (9002, 7.25, 1, '2027-01-01', NULL, NULL)",
            f"INSERT INTO ALIQUOTA_AD_VALOREM_PRODUTO VALUES (9101, '{ncm[:4]}', 9001, '2027-01-01', NULL)",
            f"INSERT INTO ALIQUOTA_AD_VALOREM_PRODUTO VALUES (9102, '{ncm[:6]}', 9002, '2027-01-01', NULL)")
        self.assertEqual(achado, ({"valor": Decimal("7.25")}, True))
        # exceção do produto de 6 dígitos que cobre o NCM informado: vale o de 4
        achado = alterar(f"INSERT INTO EXCECAO_AD_VALOREM_PRODUTO VALUES (9201, '{ncm}', 9102, '2027-01-01', NULL)")
        self.assertEqual(achado, ({"valor": Decimal("12.5")}, True))
        # exceção fora da vigência não conta
        achado = alterar(
            f"INSERT INTO EXCECAO_AD_VALOREM_PRODUTO VALUES (9202, '{ncm[:2]}', 9101, '2028-01-01', NULL)")
        self.assertEqual(achado, ({"valor": Decimal("12.5")}, True))
        # exceção vigente que cobre o prefixo do próprio produto de 4 dígitos: nada sobra
        achado = alterar(
            f"INSERT INTO EXCECAO_AD_VALOREM_PRODUTO VALUES (9203, '{ncm[:2]}', 9101, '2027-01-01', NULL)")
        self.assertEqual(achado, (None, False))

    def test_aliquota_ad_rem_produto(self):
        produtos, excecoes = self._produtos(
            "ALIQUOTA_AD_REM_PRODUTO", "AARP", "ALIQUOTA_AD_REM", "AARE", "EXCECAO_AD_REM_PRODUTO",
            "EARP", 1, self.DATA_IS)
        self.assertTrue(produtos, "a base não tem produto com Imposto Seletivo ad rem")
        ncm, = self.um("SELECT NCM_CD FROM NCM WHERE NCM_CD LIKE ? || '%' AND LENGTH(NCM_CD) = 8 "
                       "ORDER BY NCM_CD", produtos[0][1])
        esperado = self._regra(ncm, produtos, excecoes, True)
        unidade, = self.um(
            "SELECT u.UNMD_SIGLA FROM ALIQUOTA_AD_REM_PRODUTO a JOIN ALIQUOTA_AD_REM v ON v.AARE_ID = a.AARP_AARE_ID "
            "JOIN UNIDADE_MEDIDA u ON u.UNMD_ID = v.AARE_UNMD_ID WHERE a.AARP_ID = ?", produtos[0][0])
        achado = self.base.buscar_aliquota_ad_rem(ncm, 1, self.DATA_IS)
        # o DTO vem mesmo com valor nulo: é a existência da linha que liga o Imposto Seletivo
        self.assertEqual(achado, {"valor": _dec(esperado[0][1]), "unidadeMedida": unidade})
        self.assertIsNone(self.base.buscar_aliquota_ad_rem(ncm, 1, date(2026, 1, 1)))

    def test_percentual_transferencia(self):
        inicio, valor = self.um(
            "SELECT TCEG_INICIO_VIGENCIA, TCEG_VALOR FROM TRANSFERENCIA_CBS_ENTE_GOV ORDER BY TCEG_INICIO_VIGENCIA")
        data = date.fromisoformat(inicio)
        self.assertEqual(self.base.get_percentual_transferencia(data), _dec(valor))
        self.assertEqual(self.base.percentual_transferencia(data), _dec(valor))
        self.assertIsNone(self.base.get_percentual_transferencia(date(2000, 1, 1)))
        with self.assertRaises(RuntimeError):
            self.base.percentual_transferencia(date(2000, 1, 1))

    # --- NCM e anexos -------------------------------------------------------------------

    def test_ncm_e_anexo(self):
        data = date(2027, 1, 1)
        id_classificacao, prefixo = self.um(
            "SELECT NCMA_CLTR_ID, NCMA_NCM_CD FROM NCM_APLICAVEL n WHERE NOT EXISTS ("
            "SELECT 1 FROM EXCECAO_NCM_APLICAVEL e WHERE e.ENCM_NCMA_ID = n.NCMA_ID) "
            "AND NCMA_INICIO_VIGENCIA <= '2027-01-01' ORDER BY NCMA_ID")
        ncm, = self.um("SELECT NCM_CD FROM NCM WHERE NCM_CD LIKE ? || '%' AND LENGTH(NCM_CD) = 8 ORDER BY NCM_CD",
                       prefixo)
        self.assertTrue(self.base.existe_ncm(ncm, data))
        self.assertFalse(self.base.existe_ncm("00000000", data))
        self.assertTrue(self.base.tem_ncm_aplicavel_para_classificacao(id_classificacao, data))
        self.assertTrue(self.base.tem_ncm_aplicavel(ncm, id_classificacao, data))
        self.assertTrue(self.base.tem_ncm_aplicavel_sem_excecao(ncm, id_classificacao, data))
        self.assertFalse(self.base.tem_ncm_aplicavel("00000000", id_classificacao, data))
        self.assertFalse(self.base.tem_ncm_aplicavel_para_classificacao(-1, data))
        self.assertFalse(self.base.tem_nbs_aplicavel_para_classificacao(-1, data))
        nbs_classificacao, = self.um("SELECT NBSA_CLTR_ID FROM NBS_APLICAVEL ORDER BY NBSA_ID")
        self.assertTrue(self.base.tem_nbs_aplicavel_para_classificacao(nbs_classificacao, data))

    def test_excecao_ncm_aplicavel(self):
        data = date(2027, 1, 1)
        id_classificacao, codigo_excecao = self.um(
            "SELECT n.NCMA_CLTR_ID, e.ENCM_NCM_CD FROM EXCECAO_NCM_APLICAVEL e "
            "JOIN NCM_APLICAVEL n ON n.NCMA_ID = e.ENCM_NCMA_ID WHERE e.ENCM_INICIO_VIGENCIA <= '2027-01-01' "
            "ORDER BY e.ENCM_ID")
        ncm = (codigo_excecao + "00000000")[:8]
        self.assertTrue(self.base.tem_excecao_ncm_aplicavel(ncm, id_classificacao, data))
        # S: há vínculo vigente que cubra o NCM sem nenhuma exceção vigente?
        esperado = self.um(
            "SELECT EXISTS (SELECT 1 FROM NCM_APLICAVEL n WHERE n.NCMA_CLTR_ID = ? "
            "AND ? LIKE n.NCMA_NCM_CD || '%' AND n.NCMA_INICIO_VIGENCIA <= '2027-01-01' "
            "AND (n.NCMA_FIM_VIGENCIA IS NULL OR n.NCMA_FIM_VIGENCIA >= '2027-01-01') "
            "AND NOT EXISTS (SELECT 1 FROM EXCECAO_NCM_APLICAVEL e WHERE e.ENCM_NCMA_ID = n.NCMA_ID "
            "AND e.ENCM_INICIO_VIGENCIA <= '2027-01-01' "
            "AND (e.ENCM_FIM_VIGENCIA IS NULL OR e.ENCM_FIM_VIGENCIA >= '2027-01-01')))",
            id_classificacao, ncm)[0]
        self.assertEqual(self.base.tem_ncm_aplicavel_sem_excecao(ncm, id_classificacao, data), bool(esperado))

    # --- nomenclatura (NomenclaturaService) ---------------------------------------------

    def test_classificacao_excecao_515001(self):
        # NomenclaturaService.EXCEPTION_CLASSIFICACOES: anexo misto em que NF-e/NFC-e aceitam só NCM e a NFS-e
        # só NBS (Anexo IX da LC 214/2025). Fora desses documentos, ou sem vínculo com o documento, é SEM.
        data = date(2027, 1, 1)
        self.assertIn("515001", rb.EXCEPTION_CLASSIFICACOES)
        id_classificacao, = self.um("SELECT CLTR_ID FROM CLASSIFICACAO_TRIBUTARIA WHERE CLTR_CD = '515001'")
        vinculos = {rb.normalizar_sigla(t["tipoDfe"]["sigla"])
                    for t in self.base.buscar_tipo_dfe_classificacao(id_classificacao, data)}
        for sigla, esperado in (("NFE", "EXCECAO_NCM"), ("NFCE", "EXCECAO_NCM"), ("NFSE", "EXCECAO_NBS"),
                                ("DUIMP", "SEM"), ("CTE", "SEM")):
            with self.subTest(sigla=sigla):
                if sigla not in vinculos:
                    esperado = "SEM"
                self.assertEqual(self.base.determinar_nomenclatura(sigla, "515001", data), esperado)
        self.assertIn("NFE", vinculos)
        self.assertIn("NFSE", vinculos)
        with self.assertRaises(rb.SiglaDFeNaoEncontradaException):
            self.base.determinar_nomenclatura("XYZ", "515001", data)
        with self.assertRaises(rb.ClassificacaoTributariaNaoEncontradaException):
            self.base.determinar_nomenclatura("NFE", "999999", data)

    def test_nomenclatura_sem_excecao(self):
        # classificação fora da lista de exceção, com anexo de NCM e vínculo com a NF-e: NCM
        data = date(2027, 1, 1)
        linha = self.um(
            "SELECT ct.CLTR_CD FROM CLASSIFICACAO_TRIBUTARIA ct "
            "JOIN TIPO_DFE_CLASSIFICACAO t ON t.TDCL_CLTR_ID = ct.CLTR_ID JOIN TIPO_DFE td ON td.TPDF_ID = t.TDCL_TPDF_ID "
            "WHERE td.TPDF_TIPO = 55 AND ct.CLTR_CD NOT IN ('200038', '515001') "
            "AND ct.CLTR_INICIO_VIGENCIA <= '2027-01-01' AND (ct.CLTR_FIM_VIGENCIA IS NULL OR ct.CLTR_FIM_VIGENCIA >= '2027-01-01') "
            "AND t.TDCL_INICIO_VIGENCIA <= '2027-01-01' AND (t.TDCL_FIM_VIGENCIA IS NULL OR t.TDCL_FIM_VIGENCIA >= '2027-01-01') "
            "AND EXISTS (SELECT 1 FROM NCM_APLICAVEL n WHERE n.NCMA_CLTR_ID = ct.CLTR_ID "
            "AND n.NCMA_INICIO_VIGENCIA <= '2027-01-01' AND (n.NCMA_FIM_VIGENCIA IS NULL OR n.NCMA_FIM_VIGENCIA >= '2027-01-01')) "
            "ORDER BY ct.CLTR_CD")
        self.assertIsNotNone(linha)
        self.assertEqual(self.base.determinar_nomenclatura("NF-e", linha[0], data), "NCM")


if __name__ == "__main__":
    unittest.main()
