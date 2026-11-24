#!/usr/bin/env python3
"""Entrega da skill cbs-ibs-regime-regular: monta a página, o texto (Markdown) e o resumo de WhatsApp
a partir da saída do calc.py.

Os números vêm todos do corpo da calculadora oficial. Aqui só se formata, escapa e organiza: valor em
reais com vírgula, alíquota em porcentagem, estado em português. O JSON nunca é alterado e nada é somado
ou recalculado.

Uso:
    python3 render.py entrega.json --formato html --out conta.html
    python3 render.py entrega.json --formato whatsapp
    cat entrega.json | python3 render.py --formato texto

`entrega.json` = {"operacao": {...a operação como foi para o calc.py...},
                  "calculo": {...a saída do calc.py, inteira...},
                  "sugestoes": {"<número do item>": {...saída do sugestao.py...}} | null,
                  "exemplo": bool,
                  "observacoes": ["texto curto", ...]}   (opcional)

`operacao` (só para mostrar): id, data (AAAA-MM-DD ou DD/MM/AAAA) e itens[{numero, descricao}]. A descrição
é a única coisa que a conta não traz de volta.

`calculo` (a saída do calc.py): status, corpo (objetos, total ou, em erro, errors/title/detail),
versao {calculadora, base, conferida_em, conferencia?}, simulado, aviso, aviso_origem e, quando a conta usou
um cenário de alíquota, cenario {cenario, nome, cbs, ibs_estadual, ibs_municipal, hipotese_cd2, nota,
checked_at, fontes}. Por item (objetos[]): nObj, estadoItem (CALCULADO, CALCULO_SIMULADO, ...) e
tribCalc.IBSCBS (gIBSCBS com vBC, pCBS/vCBS, pIBSUF/vIBSUF, pIBSMun/vIBSMun, vIBS, e memoriaCalculo com
cbs, ibsUF e ibsMun) e tribCalc.IS (vBCIS, pIS, adRemIS, vIS e memoriaCalculo); item com erro traz
`erros` [{title, code, detail}]. Total: total.tribCalc com IBSCBSTot (vBCIBSCBS, gCBS.vCBS, gIBS.vIBS,
gIBS.gIBSUF.vIBSUF, gIBS.gIBSMun.vIBSMun) e ISTot.vIS.
`versao.conferencia` {casos, iguais} é a conferência com a calculadora oficial (conferencia/.../meta.json);
o calc.py não a traz, então, sem ela na entrega, o render lê da base local (o mesmo meta), se der.

`exemplo` marca a entrega inteira como exemplo (por exemplo, alíquota de Imposto Seletivo de exemplo).
`observacoes` são fatos que a pessoa precisa ler ao lado do número (no máximo 5, até 300 caracteres cada).

Formatos: html, texto, whatsapp. Só biblioteca padrão (Python 3.10+).
"""
import argparse
import base64
import json
import re
import sys
import unicodedata
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation, localcontext
from pathlib import Path
from urllib.parse import urlparse

# brand.py fica ao lado deste arquivo no pacote final (o sync copia). No
# repositório (layout core) ele mora em cd2-super/shared/scripts.
_HERE = Path(__file__).resolve().parent
_PATHS = [_HERE]
if len(_HERE.parents) > 2:
    _PATHS.append(_HERE.parents[2] / "shared" / "scripts")
for _p in _PATHS:
    if _p.is_dir() and str(_p) not in sys.path:
        sys.path.append(str(_p))

import brand  # noqa: E402
from brand import esc  # noqa: E402

SKILL = "cbs-ibs-regime-regular"
SKILL_TITLE = "CBS, IBS e Imposto Seletivo no regime regular"
REGRA = "regime regular"
FORMATOS = ("html", "texto", "whatsapp")
CD2_URL = "https://cd2.com.br/"
ARCHEO_URL = "https://archeo.cd2.com.br/"
CALC_IMPOSTO_URL = "https://calc-imposto.cd2.com.br/"
RECEITA_URL = "https://github.com/ReceitaFederal/calculadora-rtc-regime-regular"
INICIO_SIMULACAO = 2027
LIMITE_WHATSAPP = 1000
MAX_OBSERVACOES = 5
LIMITE_OBSERVACAO = 300
TITULO_OBSERVACOES = "Observações"
MAIS_OBSERVACOES = "Há mais observações na página completa."

ESTADOS = {
    "CALCULADO": ("Calculado", "green"),
    "CALCULO_SIMULADO": ("Calculado (simulado)", "amber"),
    "NAO_IMPLEMENTADO": ("Ainda não tratado pela calculadora oficial", "amber"),
    "INCONSISTENCIA_ENTRADA": ("Dado de entrada com problema", "red"),
    "FALHA_TECNICA": ("Falha técnica", "red"),
}
ESTADOS_COM_CONTA = ("CALCULADO", "CALCULO_SIMULADO")

AVISO_FIXO = (
    "Resultado gerencial. Esta skill foi feita pela CD2 Tech com o código e as tabelas da calculadora "
    "oficial e não é aplicativo da Receita Federal. Confira com o seu contador."
)
AVISO_FIXO_CURTO = "Resultado gerencial, não é aplicativo da Receita Federal. Confira com o seu contador."
AVISO_SIMULADO = (
    "Resultado simulado: as alíquotas de 2027 em diante são cenário ou informadas, não as definitivas. "
    "Confira com o contador."
)
AVISO_SIMULADO_CURTO = "SIMULADO: alíquotas de 2027 em diante são cenário ou informadas, não as definitivas."
HIPOTESE_CD2 = (
    "A divisão do IBS entre estado e município é hipótese da CD2, sem fonte oficial. "
    "O total por item não muda com ela."
)
HIPOTESE_CD2_CURTA = "A divisão do IBS entre estado e município é hipótese da CD2."
ALIQUOTAS_INFORMADAS = "Alíquotas informadas por você ou vindas da operação, sem cenário padrão da CD2."
AVISO_SUGESTAO = "Classificação sugerida pela CD2, confirme com o contador."
SELO_SUGESTAO = "O selo vale para a conta, não para a classificação sugerida."
FAIXA_EXEMPLO = (
    "Exemplo ilustrativo. Os números e a alíquota do Imposto Seletivo são de exemplo, não são da sua empresa."
)
SEM_ITEM = "Nenhum item foi calculado."
SEM_SELO = "Sem item calculado, não há conta para o selo de conferência."
TITULO_CONVITE = "Quer ver a loja inteira, pela planilha de produtos?"
TEXTO_CONVITE = "Use a calc-imposto da CD2: ela calcula todos os produtos de uma vez, a partir da planilha."
TITULO_COMERCIAL = "Quer levar esta conta para o dia a dia da empresa?"
TEXTO_COMERCIAL = (
    "Testar é livre. Para usar na rotina da empresa, com suporte e responsabilidade, fale com a CD2."
)
TITULO_ERP = "Seus números estão no ERP?"
TEXTO_ERP = "O Archeo MCP Creator, da CD2, cria a conexão para puxar os dados direto do sistema, sem digitar."

_TRAVESSOES = {chr(0x2014): "-", chr(0x2013): "-"}


def _txt(valor) -> str:
    """Texto de fora, limpo: uma linha só, sem controle, sem separador de linha, sem travessão.
    Todo espaço Unicode vira espaço comum."""
    if valor is None:
        return ""
    saida = []
    for c in str(valor):
        categoria = unicodedata.category(c)
        if c in "\n\r\t" or categoria == "Zs":
            saida.append(" ")
        elif categoria in ("Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"):
            continue
        else:
            saida.append(_TRAVESSOES.get(c, c))
    return re.sub(r" {2,}", " ", "".join(saida)).strip()


_JS = re.compile(r"java\s*script\s*:", re.IGNORECASE)


def _sem_js(texto: str) -> str:
    while _JS.search(texto):
        texto = _JS.sub("", texto)
    return texto


def _txt_md(valor) -> str:
    """Texto de fora seguro em Markdown: sem HTML, sem link, sem crase e sem quebrar tabela."""
    texto = _txt(valor).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    texto = texto.replace("`", "'").replace("[", "(").replace("]", ")").replace("|", "/")
    return re.sub(r"([\\*_#~])", r"\\\1", _sem_js(texto))


def _txt_wa(valor) -> str:
    """Texto de fora para o WhatsApp: sem HTML, sem colchete, sem crase."""
    texto = re.sub(r"<[^>]*>", "", _txt(valor))
    texto = texto.replace("<", "").replace(">", "").replace("`", "'").replace("[", "(").replace("]", ")")
    return _sem_js(texto)


_LIMPADORES = {"html": _txt, "texto": _txt_md, "whatsapp": _txt_wa}


def _dec(valor):
    if isinstance(valor, bool) or valor is None:
        return None
    texto = str(valor).strip()
    if len(texto) > 60:
        return None
    try:
        d = Decimal(texto)
    except (InvalidOperation, ValueError):
        return None
    return d if d.is_finite() else None


def _arredondar(d: Decimal, casas: int) -> Decimal:
    with localcontext() as contexto:
        contexto.prec = 120  # o valor tem até 60 caracteres: o arredondamento não pode estourar a precisão
        return d.quantize(Decimal(1).scaleb(-casas), rounding=ROUND_HALF_EVEN)


def _milhar(inteiro: str) -> str:
    return f"{int(inteiro):,}".replace(",", ".")


def _corta(texto: str, limite: int) -> str:
    return texto if len(texto) <= limite else texto[: limite - 3].rstrip() + "..."


def _brl(valor) -> str:
    d = _dec(valor)
    if d is None:
        return _corta(_txt(valor), 30) or "s/d"
    d = _arredondar(d, 2)
    inteiro, frac = f"{abs(d):.2f}".split(".")
    sinal = "-" if d < 0 else ""
    return f"{sinal}R$ {_milhar(inteiro)},{frac}"


def _pct(valor) -> str:
    """Porcentagem com no mínimo uma casa e no máximo quatro: 8,8%, 8,85%, 0,05%."""
    d = _dec(valor)
    if d is None:
        return _corta(_txt(valor), 30) or "s/d"
    d = _arredondar(d, 4)
    inteiro, frac = f"{abs(d):.4f}".split(".")
    frac = frac.rstrip("0") or "0"
    sinal = "-" if d < 0 else ""
    return f"{sinal}{_milhar(inteiro)},{frac}%"


def _pct2(valor) -> str:
    """Como _pct, mas com pelo menos duas casas (tabela de itens): 8,80%, 8,85%."""
    texto = _pct(valor)
    if _dec(valor) is None:
        return texto
    corpo = texto[:-1]
    frac = corpo.split(",")[1]
    return corpo + "0" * (2 - len(frac)) + "%" if len(frac) < 2 else texto


def _inteiro_ou_decimal(valor) -> str:
    d = _dec(valor)
    if d is None or abs(d) > Decimal("100000"):
        return ""
    texto = f"{_arredondar(d, 2):f}"
    if "." in texto:
        texto = texto.rstrip("0").rstrip(".")
    return texto.replace(".", ",")


def _inteiro_br(valor) -> str:
    d = _dec(valor)
    if d is None or d != d.to_integral_value() or abs(d) > Decimal("100000000"):
        return ""
    return _milhar(str(abs(int(d))))


_DATA_ISO = re.compile(r"\d{4}-\d{2}-\d{2}")
_DATA_BR = re.compile(r"\d{2}/\d{2}/\d{4}")


def _data(texto) -> str:
    texto = _txt(texto)
    if _DATA_ISO.match(texto):
        return f"{texto[8:10]}/{texto[5:7]}/{texto[:4]}"
    return texto


def _ano_da_data(texto):
    texto = _txt(texto)
    achou = re.match(r"^(\d{4})-\d{2}-\d{2}", texto) or re.match(r"^\d{2}/\d{2}/(\d{4})", texto)
    return int(achou.group(1)) if achou else None


def _dict(valor) -> dict:
    return valor if isinstance(valor, dict) else {}


def _lista(valor) -> list:
    return valor if isinstance(valor, list) else []


_VERSAO = re.compile(r"[vV]?\d+(?:\.\d+)*")


def _dominio(url) -> str:
    """Só o domínio de um endereço (para o resumo de WhatsApp): www.gov.br vira gov.br."""
    host = urlparse(_txt(url)).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _endereco_seguro(url) -> str:
    """Endereço http(s) de fonte, sem espaço nem aspas; qualquer outra coisa não vira link."""
    url = _txt(url)
    return url if re.fullmatch(r"https?://[^\s\"'<>]{1,300}", url) else ""


def _conferencia_da_base() -> dict:
    """{casos, iguais} do meta da base local, ou {} se a base não estiver à mão."""
    try:
        import rr_base  # noqa: WPS433  (só aqui: o render não depende da base para formatar)
        with rr_base.Base() as base:
            conferencia = _dict(base.meta()).get("conferencia")
        return conferencia if isinstance(conferencia, dict) else {}
    except Exception:  # sem base, o selo sai sem o N de N
        return {}


# ------------------------------------------------------------------ leitura da entrega

class _Entrega:
    """Tudo o que a entrega precisa, lido uma vez. Todo texto que vem de fora passa por `self.t`, o
    limpador do formato: o que sai daqui já é seguro para o formato (o HTML ainda escapa na saída)."""

    def __init__(self, dados: dict, formato: str, conferencia_da_base: bool = False):
        self.t = _LIMPADORES[formato]
        calculo = dados["calculo"]
        self.status = calculo.get("status")
        corpo = calculo.get("corpo")
        self.corpo = corpo if isinstance(corpo, dict) else {}
        versao = _dict(calculo.get("versao"))
        self.v_calculadora = self._versao(versao.get("calculadora"))
        self.v_base = self._versao(versao.get("base"))
        conferida = _txt(versao.get("conferida_em"))
        self.conferida = conferida if _DATA_ISO.fullmatch(conferida) else ""
        conferencia = _dict(versao.get("conferencia"))
        if not conferencia and conferida_da_base_ok(conferencia_da_base):
            conferencia = _conferencia_da_base()
        self.casos = _inteiro_br(conferencia.get("casos"))
        self.iguais = _inteiro_br(conferencia.get("iguais"))
        self.operacao = _dict(dados.get("operacao"))
        textos = [o for o in _lista(dados.get("observacoes")) if isinstance(o, str)]
        limpas = (self.t(_corta(_txt(o), LIMITE_OBSERVACAO)) for o in textos)
        self.observacoes = [o for o in limpas if o][:MAX_OBSERVACOES]
        self.exemplo = bool(dados.get("exemplo"))
        sugestoes = dados.get("sugestoes")
        self.sugestoes = sugestoes if isinstance(sugestoes, dict) else {}
        self.cenario = self._cenario(_dict(calculo.get("cenario")))

        data = _txt(self.operacao.get("data"))
        self.data_iso = data
        self.op_id = self.t(self.operacao.get("id"))
        self.nivel_operacao = not isinstance(self.corpo.get("objetos"), list)
        self.erros_operacao = self._erros_da_operacao() if self.nivel_operacao else []
        self.itens = [] if self.nivel_operacao else self._itens()
        self.calculados = [i for i in self.itens if i["estado"] in ESTADOS_COM_CONTA]
        self.problemas = [i for i in self.itens if i["estado"] not in ESTADOS_COM_CONTA]
        self.avisos = [t for t in (self.t(a) for a in _lista(self.corpo.get("avisos"))) if t]
        self.normalizacoes = [t for t in (self.t(n) for n in _lista(calculo.get("normalizacoes"))) if t]

        ano = _ano_da_data(data)
        self.simulado = (calculo.get("simulado") is True or (ano is not None and ano >= INICIO_SIMULACAO)
                         or any(i["estado"] == "CALCULO_SIMULADO" for i in self.itens)) and not self.nivel_operacao
        tributos = _dict(_dict(_dict(self.corpo.get("total")).get("tribCalc")))
        ibscbs, is_tot = _dict(tributos.get("IBSCBSTot")), _dict(tributos.get("ISTot"))
        gibs = _dict(ibscbs.get("gIBS"))
        self.total = {
            "base": self.num(ibscbs.get("vBCIBSCBS")),
            "cbs": self.num(_dict(ibscbs.get("gCBS")).get("vCBS")),
            "ibs": self.num(gibs.get("vIBS")),
            "ibs_uf": self.num(_dict(gibs.get("gIBSUF")).get("vIBSUF")),
            "ibs_mun": self.num(_dict(gibs.get("gIBSMun")).get("vIBSMun")),
            "is": self.num(is_tot.get("vIS")),
        }
        self.tem_total = bool(self.calculados) and _dec(self.total["cbs"]) is not None \
            and _dec(self.total["ibs"]) is not None
        if self.tem_total and _dec(self.total["is"]) is None:
            self.total["is"] = "0.00"  # o total sem ISTot é um total sem Imposto Seletivo
        self.alguma_sugestao = any(i["sugestao"] for i in self.itens)

    # --- campos de valor e de versão
    def num(self, valor):
        """Valor numérico do corpo oficial: o texto original se for número, senão texto limpo e curto."""
        if valor is None:
            return None
        if _dec(valor) is not None:
            return str(valor).strip()
        return _corta(self.t(valor), 30)

    @staticmethod
    def _versao(valor) -> str:
        texto = _txt(valor)
        if not texto:
            return ""
        return texto if len(texto) <= 20 and _VERSAO.fullmatch(texto) else "desconhecida"

    # --- cenário de alíquota
    def _cenario(self, c: dict) -> dict:
        if not c:
            return {}
        fontes = [_endereco_seguro(f) for f in _lista(c.get("fontes"))]
        conferido = _txt(c.get("checked_at"))
        return {"nome": self.t(c.get("nome")) or self.t(c.get("cenario")),
                "cbs": self.num(c.get("cbs")), "ibs_uf": self.num(c.get("ibs_estadual")),
                "ibs_mun": self.num(c.get("ibs_municipal")),
                "hipotese": c.get("hipotese_cd2") is True,
                "conferido": _data(conferido) if _DATA_ISO.fullmatch(conferido) else self.t(conferido),
                "fontes": [f for f in fontes if f][:6]}

    @property
    def linha_cenario(self) -> str:
        c = self.cenario
        partes = [f"CBS {_pct(c['cbs'])}" if c["cbs"] is not None else "",
                  f"IBS estadual {_pct(c['ibs_uf'])}" if c["ibs_uf"] is not None else "",
                  f"IBS municipal {_pct(c['ibs_mun'])}" if c["ibs_mun"] is not None else ""]
        texto = f"Alíquotas do cenário {c['nome']}: " + ", ".join(p for p in partes if p)
        return texto + (f", conferidas em {c['conferido']}" if c["conferido"] else "")

    # --- erros da operação (400, 422, 500...)
    def _erros_da_operacao(self) -> list:
        erros = []
        for e in _lista(self.corpo.get("errors")):
            if isinstance(e, dict):
                erros.append({"titulo": self.t(e.get("title")), "codigo": self.t(e.get("code")),
                              "texto": self.t(e.get("detail"))})
        if not erros:
            titulo = self.t(self.corpo.get("title")) or "A calculadora oficial não fez a conta"
            erros.append({"titulo": titulo, "codigo": "", "texto": self.t(self.corpo.get("detail"))})
        return erros

    @property
    def titulo_erro_operacao(self) -> str:
        if self.status in (400, 422):
            return "A conta não foi feita: a operação tem erro"
        return "Falha técnica: a calculadora oficial não fez a conta"

    # --- itens
    def _item_da_operacao(self, posicao: int, numero: str) -> dict:
        itens = [i for i in _lista(self.operacao.get("itens")) if isinstance(i, dict)]
        for item in itens:
            if _txt(item.get("numero")) == numero:
                return item
        return itens[posicao] if posicao < len(itens) else {}

    def _itens(self) -> list:
        saida = []
        for posicao, obj in enumerate(o for o in _lista(self.corpo.get("objetos")) if isinstance(o, dict)):
            bruto = _txt(obj.get("nObj"))
            numero = bruto if re.fullmatch(r"\d{1,6}", bruto) else str(posicao + 1)
            estado = obj.get("estadoItem") if isinstance(obj.get("estadoItem"), str) else ""
            rotulo, cor = ESTADOS.get(estado, (self.t(estado) or "Estado desconhecido", "amber"))
            trib = _dict(obj.get("tribCalc"))
            ibscbs, imposto = _dict(trib.get("IBSCBS")), _dict(trib.get("IS"))
            g = _dict(ibscbs.get("gIBSCBS"))
            origem = self._item_da_operacao(posicao, numero)
            descricao = ""
            for campo in ("descricao", "nome", "produto", "xProd"):
                if self.t(origem.get(campo)):
                    descricao = self.t(origem.get(campo))
                    break
            sugestao = self.sugestoes.get(numero)
            memoria = _dict(ibscbs.get("memoriaCalculo"))
            saida.append({
                "numero": numero, "estado": estado, "rotulo": rotulo, "cor": cor, "descricao": descricao,
                "base": self.num(g.get("vBC")),
                "p_cbs": self.num(_dict(g.get("gCBS")).get("pCBS")), "v_cbs": self.num(_dict(g.get("gCBS")).get("vCBS")),
                "p_ibs_uf": self.num(_dict(g.get("gIBSUF")).get("pIBSUF")),
                "p_ibs_mun": self.num(_dict(g.get("gIBSMun")).get("pIBSMun")), "v_ibs": self.num(g.get("vIBS")),
                "v_is": self.num(imposto.get("vIS")),
                "is_detalhe": self._is_detalhe(imposto),
                "sugestao": self._sugestao(sugestao) if isinstance(sugestao, dict) else None,
                "motivos": self._motivos(obj),
                "memoria": [(nome, self.t(memoria.get(chave))) for chave, nome in
                            (("cbs", "CBS"), ("ibsUF", "IBS estadual"), ("ibsMun", "IBS municipal"))
                            if self.t(memoria.get(chave))],
                "memoria_is": self.t(imposto.get("memoriaCalculo")),
            })
        return saida

    def _is_detalhe(self, imposto: dict) -> str:
        if not imposto:
            return ""
        partes = []
        if _dec(imposto.get("vBCIS")) is not None:
            partes.append(f"base {_brl(imposto['vBCIS'])}")
        if _dec(imposto.get("pIS")) is not None:
            partes.append(f"alíquota {_pct(imposto['pIS'])}")
        if _dec(imposto.get("adRemIS")) not in (None, Decimal(0)):
            partes.append(f"ad rem {_brl(imposto['adRemIS'])}")
        return ", ".join(partes)

    def _sugestao(self, s: dict) -> dict:
        imposto = _dict(s.get("imposto_seletivo"))
        reducao = _inteiro_ou_decimal(s.get("reducao"))
        return {"classificacao": self.t(s.get("cClassTrib")), "cst": self.t(s.get("cst")),
                "descricao": self.t(s.get("descricao")),
                "reducao": reducao if reducao and _dec(s.get("reducao")) != 0 else "",
                "is_texto": self.t(imposto.get("texto")) if imposto.get("na_lista") is True else ""}

    def _motivos(self, obj: dict) -> list:
        motivos = []
        for e in _lista(obj.get("erros")):
            if isinstance(e, dict):
                motivos.append({"titulo": self.t(e.get("title")), "codigo": self.t(e.get("code")),
                                "texto": self.t(e.get("detail"))})
        return motivos

    # --- selo, versão e rodapé
    def _versoes(self) -> str:
        partes = [f"calculadora {self.v_calculadora}" if self.v_calculadora else "",
                  f"base {self.v_base}" if self.v_base else ""]
        return ", ".join(p for p in partes if p)

    def _selo(self, com_data: bool) -> str:
        # Sem item calculado não há conta para selar: sobra só a versão (veja versao_sem_selo)
        if not self.calculados or (not self.v_calculadora and not self.v_base):
            return ""
        texto = f"Conta conferida com a calculadora oficial do regime regular ({self._versoes()})"
        if com_data and self.conferida:
            texto += f" em {_data(self.conferida)}"
        if com_data and self.casos and self.iguais:
            texto += f": {self.iguais} de {self.casos} casos iguais"
        return texto + "."

    @property
    def selo(self) -> str:
        return self._selo(True)

    @property
    def selo_curto(self) -> str:
        return self._selo(False)

    @property
    def versao_sem_selo(self) -> str:
        if self.calculados or (not self.v_calculadora and not self.v_base):
            return ""
        return f"{SEM_SELO} Calculadora oficial do regime regular ({self._versoes()})."

    @property
    def selo_extra(self) -> str:
        return SELO_SUGESTAO if self.alguma_sugestao and self.selo else ""

    @property
    def data_do_rodape(self) -> str:
        return _data(self.conferida) if self.conferida else date.today().strftime("%d/%m/%Y")

    @property
    def linha_operacao(self) -> str:
        partes = []
        if self.op_id:
            partes.append(f"Operação {self.op_id}")
        data = self.t(self.data_iso)
        if data:
            partes.append(f"data {_corta(_data(data), 30)}")
        return ", ".join(partes)

    @property
    def contagem(self) -> str:
        n, k = len(self.itens), len(self.problemas)
        texto = f"{n} {'item' if n == 1 else 'itens'}, {len(self.calculados)} "
        texto += "calculado" if len(self.calculados) == 1 else "calculados"
        return texto + (f", {k} com problema" if k else "")

    @property
    def sem_total(self) -> str:
        return "Total indisponível" if self.calculados else SEM_ITEM.rstrip(".")

    @property
    def aliquotas_ibs_item(self):
        return None


def conferida_da_base_ok(pedido: bool) -> bool:
    return bool(pedido)


def _linha_sugestao(s: dict) -> str:
    texto = f"cClassTrib {s['classificacao']}" if s["classificacao"] else "classificação"
    if s["cst"]:
        texto += f" (CST {s['cst']})"
    if s["reducao"]:
        texto += f", redução de {s['reducao']}%"
    texto += f": {s['descricao']}" if s["descricao"] else ""
    return texto


def _ibs_pct(i: dict) -> str:
    """Alíquota do IBS de um item: estadual + municipal, como a calculadora devolve."""
    if i["p_ibs_uf"] is None and i["p_ibs_mun"] is None:
        return ""
    return " + ".join(_pct2(p) for p in (i["p_ibs_uf"], i["p_ibs_mun"]) if p is not None)


def _extras_do_total(e: _Entrega) -> list:
    """Linhas de apoio dos totais: IBS dividido em estadual e municipal."""
    t = e.total
    return [(nome, _brl(t[chave])) for chave, nome in (("ibs_uf", "IBS estadual"), ("ibs_mun", "IBS municipal"))
            if _dec(t.get(chave)) is not None]


# ------------------------------------------------------------------ links

def _links(plataforma: str) -> dict:
    return {
        "calc": brand.link(CALC_IMPOSTO_URL, SKILL),
        "whatsapp": brand.whatsapp_link(SKILL_TITLE, plataforma),
        "cd2": brand.link(CD2_URL, SKILL),
        "archeo": brand.link(ARCHEO_URL, SKILL),
    }


def _a(href, texto, classe="") -> str:
    atributo = f' class="{classe}"' if classe else ""
    return f'<a{atributo} href="{esc(href)}" target="_blank" rel="noopener">{esc(texto)}</a>'


# ------------------------------------------------------------------ HTML

def _achar(*candidatos):
    for caminho in candidatos:
        if caminho.is_file():
            return caminho
    return None


def _template() -> str:
    caminho = _achar(_HERE.parent / "assets" / "template.html", _HERE / "assets" / "template.html",
                     _HERE / "template.html")
    if caminho is None:
        raise FileNotFoundError("assets/template.html")
    return caminho.read_text(encoding="utf-8")


def _font_face() -> str:
    """Manrope embutida (OFL), se o arquivo vier no pacote. Sem ele, fica o system-ui."""
    caminho = _achar(
        _HERE.parent / "assets" / "fonts" / "Manrope-Variable.woff2",
        _HERE / "assets" / "fonts" / "Manrope-Variable.woff2",
        _HERE / "fonts" / "Manrope-Variable.woff2",
        _HERE / "Manrope-Variable.woff2",
    )
    if caminho is None:
        return ""
    dados = base64.b64encode(caminho.read_bytes()).decode("ascii")
    return (
        "@font-face { font-family: Manrope; font-weight: 200 800; font-display: swap; "
        f'src: url(data:font/woff2;base64,{dados}) format("woff2"); }}'
    )


def _preencher(modelo: str, valores: dict) -> str:
    # Uma passada só: o texto inserido nunca é relido como placeholder.
    # Placeholders desconhecidos (como o de UTM, trocado pelo sync) ficam como estão.
    return re.sub(r"\{\{([A-Z_]+)\}\}", lambda m: valores.get(m.group(1), m.group(0)), modelo)


def _html_motivos(motivos: list) -> str:
    if not motivos:
        return ""
    itens = []
    for m in motivos:
        cabeca = f"<b>{esc(m['titulo'])}</b> " if m["titulo"] else ""
        if m["codigo"]:
            cabeca += f'<span class="cod">{esc(m["codigo"])}</span>'
        corpo = f"<span>{esc(m['texto'])}</span>" if m["texto"] else ""
        itens.append(f"<li>{('<span>' + cabeca + '</span>') if cabeca else ''}{corpo}</li>")
    return f'<ul class="motivos">{"".join(itens)}</ul>'


def _celula(rotulo: str, texto: str) -> str:
    return f'<td class="n" data-label="{rotulo}">{esc(texto)}</td>'


def _valor(formatar, valor) -> str:
    return formatar(valor) if valor is not None else ""


def _html_linha_item(i: dict) -> str:
    extra = ""
    if i["sugestao"]:
        extra += (f'<span><span class="sugerida">{esc(AVISO_SUGESTAO)}</span> '
                  f'{esc(_linha_sugestao(i["sugestao"]))}</span>')
        if i["sugestao"]["is_texto"]:
            extra += f'<span><b>Imposto Seletivo:</b> {esc(i["sugestao"]["is_texto"])}</span>'
    if i["is_detalhe"]:
        extra += f'<span><b>Imposto Seletivo:</b> {esc(i["is_detalhe"])}.</span>'
    extra += _html_motivos(i["motivos"])
    desc = f'<span class="item-desc">{esc(i["descricao"])}</span>' if i["descricao"] else ""
    extra_html = f'<div class="item-extra">{extra}</div>' if extra else ""
    return (
        f'<tr><th scope="row">Item {esc(i["numero"])}{desc}{extra_html}</th>'
        f'<td data-label="Situação"><span class="pill {i["cor"]}">{esc(i["rotulo"])}</span></td>'
        + _celula("Base", _valor(_brl, i["base"])) + _celula("Alíquota CBS", _valor(_pct2, i["p_cbs"]))
        + _celula("CBS", _valor(_brl, i["v_cbs"])) + _celula("Alíquota IBS", _ibs_pct(i))
        + _celula("IBS", _valor(_brl, i["v_ibs"])) + _celula("IS", _valor(_brl, i["v_is"])) + "</tr>"
    )


def _html_itens(e: _Entrega) -> str:
    if not e.itens:
        return ""
    linhas = "".join(_html_linha_item(i) for i in e.itens)
    rodape = ""
    if e.tem_total:
        t = e.total
        rodape = (
            '<tfoot><tr><th scope="row">Total</th><td data-label="Situação"></td>'
            f'{_celula("Base", _valor(_brl, t["base"]))}<td data-label="Alíquota CBS"></td>'
            f'{_celula("CBS", _brl(t["cbs"]))}<td data-label="Alíquota IBS"></td>'
            f'{_celula("IBS", _brl(t["ibs"]))}{_celula("IS", _brl(t["is"]))}</tr></tfoot>'
        )
    return (
        '<section class="secao" aria-labelledby="t-itens"><h2 class="heading-md" id="t-itens">Itens da operação</h2>'
        '<table class="tabela"><thead><tr><th scope="col">Item</th><th scope="col">Situação</th>'
        '<th scope="col" class="n">Base</th><th scope="col" class="n">Alíquota CBS</th>'
        '<th scope="col" class="n">CBS</th><th scope="col" class="n">Alíquota IBS (estadual + municipal)</th>'
        '<th scope="col" class="n">IBS</th><th scope="col" class="n">IS</th></tr></thead>'
        f"<tbody>{linhas}</tbody>{rodape}</table></section>"
    )


def _html_hero(e: _Entrega) -> str:
    if e.nivel_operacao:
        itens = []
        for erro in e.erros_operacao:
            cabeca = f"<b>{esc(erro['titulo'])}</b>" if erro["titulo"] else ""
            if erro["codigo"]:
                cabeca += f' <span class="cod">{esc(erro["codigo"])}</span>'
            texto = f"<span>{esc(erro['texto'])}</span>" if erro["texto"] else ""
            itens.append(f"<li><span>{cabeca}</span>{texto}</li>")
        return (
            '<section class="bloco erro-op" role="alert" aria-labelledby="t-total">'
            '<p class="eyebrow">ERRO NA OPERAÇÃO</p>'
            f'<h1 class="display-lg" id="t-total">{esc(e.titulo_erro_operacao)}</h1>'
            f'<ul>{"".join(itens)}</ul></section>'
        )
    if not e.tem_total:
        return (
            '<section class="bloco b-raised hero" aria-labelledby="t-total"><p class="eyebrow mut">CBS, IBS E IS</p>'
            f'<h1 class="display-lg" id="t-total">{esc(e.sem_total)}</h1>'
            f'<p class="stat-line">{esc(e.contagem)}. Veja abaixo o detalhe de cada item.</p></section>'
        )
    t = e.total
    extras = "".join(f'<p class="stat-line">{esc(nome)}: <b class="num">{esc(valor)}</b></p>'
                     for nome, valor in _extras_do_total(e))
    return (
        '<section class="bloco b-brand hero" aria-labelledby="t-total"><p class="eyebrow">TOTAL DA OPERAÇÃO</p>'
        '<h1 class="display-lg" id="t-total">CBS, IBS e Imposto Seletivo no regime regular</h1>'
        '<div class="hero-numeros">'
        f'<div><p class="stat-line">CBS</p><p class="stat">{esc(_brl(t["cbs"]))}</p></div>'
        f'<div><p class="stat-line">IBS</p><p class="stat">{esc(_brl(t["ibs"]))}</p></div>'
        f'<div><p class="stat-line">Imposto Seletivo (IS)</p><p class="stat">{esc(_brl(t["is"]))}</p></div></div>'
        f'<div class="hero-extra">{extras}<p class="stat-line">{esc(e.contagem)}</p></div></section>'
    )


def _html_observacoes(e: _Entrega) -> str:
    if not e.observacoes:
        return ""
    itens = "".join(f"<li>{esc(o)}</li>" for o in e.observacoes)
    return (f'<section class="secao" aria-labelledby="t-obs"><h2 class="heading-sm" id="t-obs">{TITULO_OBSERVACOES}</h2>'
            f'<ul class="lista">{itens}</ul></section>')


def _html_simulado(e: _Entrega) -> str:
    if not e.simulado:
        return ""
    return f'<div class="aviso destaque" role="note"><p><b>{esc(AVISO_SIMULADO)}</b></p></div>'


def _html_cenario(e: _Entrega) -> str:
    if e.nivel_operacao:
        return ""
    if not e.cenario:
        return ('<section class="cenario" aria-label="Alíquotas usadas"><p class="eyebrow">ALÍQUOTAS USADAS</p>'
                f'<p>{esc(ALIQUOTAS_INFORMADAS)}</p></section>')
    fontes = "".join(f"<li>{_a(f, f)}</li>" for f in e.cenario["fontes"])
    fontes = f'<p class="legenda">Fontes:</p><ul>{fontes}</ul>' if fontes else ""
    hipotese = f'<p><b>{esc(HIPOTESE_CD2)}</b></p>' if e.cenario["hipotese"] else ""
    return ('<section class="cenario" aria-label="Alíquotas usadas"><p class="eyebrow">ALÍQUOTAS USADAS</p>'
            f'<p>{esc(e.linha_cenario)}.</p>{hipotese}{fontes}</section>')


def _html_memoria(e: _Entrega) -> str:
    blocos = []
    for i in e.calculados:
        partes = "".join(f"<p><b>{nome}:</b> {esc(texto)}</p>" for nome, texto in i["memoria"])
        if i["memoria_is"]:
            partes += f"<p><b>Imposto Seletivo:</b> {esc(i['memoria_is'])}</p>"
        if partes:
            blocos.append(f"<div><h3>Item {esc(i['numero'])}</h3>{partes}</div>")
    if not blocos:
        return ""
    return (
        '<section class="secao"><details class="memoria"><summary>Memória de cálculo</summary>'
        f'<div class="memoria-itens">{"".join(blocos)}</div></details></section>'
    )


def _html_avisos_operacao(e: _Entrega) -> str:
    saida = ""
    if e.avisos:
        itens = "".join(f"<li>{esc(a)}</li>" for a in e.avisos)
        saida += ('<section class="secao"><h2 class="heading-sm">Avisos da operação</h2>'
                  f'<ul class="lista">{itens}</ul></section>')
    if e.normalizacoes:
        itens = "".join(f"<li>{esc(n)}</li>" for n in e.normalizacoes)
        saida += ('<section class="secao"><h2 class="heading-sm">O que arrumamos na entrada</h2>'
                  f'<ul class="lista">{itens}</ul></section>')
    return saida


def _html_selo(e: _Entrega) -> str:
    partes = [t for t in (e.selo or e.versao_sem_selo, e.selo_extra) if t]
    if not partes:
        return ""
    paragrafos = "".join(f"<p>{esc(t)}</p>" for t in partes)
    if not e.selo:
        return f'<section class="selo" aria-label="Versão da calculadora"><p class="eyebrow">VERSÃO DA CALCULADORA</p>{paragrafos}</section>'
    return f'<section class="selo" aria-label="Selo de conferência"><p class="eyebrow">CONTA CONFERIDA</p>{paragrafos}</section>'


def _html_avisos_fixos() -> str:
    return (
        f'<div class="aviso"><p><b>{esc(AVISO_FIXO)}</b></p></div>'
        f'<p class="legenda">Fonte do código e das tabelas: {_a(RECEITA_URL, "repositório da calculadora da Receita Federal")}.</p>'
    )


def _html_comercial(links: dict) -> str:
    return (
        '<section class="bloco b-brand comercial" aria-labelledby="t-convite">'
        f'<h2 class="heading-md" id="t-convite">{esc(TITULO_CONVITE)}</h2><p>{esc(TEXTO_CONVITE)}</p>'
        f'<div class="botoes">{_a(links["calc"], "Abrir a calc-imposto", "cd2-botao")}</div></section>'
        '<section class="bloco b-raised comercial" aria-labelledby="t-comercial">'
        f'<h2 class="heading-md" id="t-comercial">{esc(TITULO_COMERCIAL)}</h2><p>{esc(TEXTO_COMERCIAL)}</p>'
        f'<div class="botoes">{_a(links["whatsapp"], "Falar com a CD2 no WhatsApp", "cd2-botao")}'
        f'{_a(links["cd2"], "Conhecer a CD2", "cd2-botao sec")}</div>'
        f'<div class="extras"><p><b>{esc(TITULO_ERP)}</b> {esc(TEXTO_ERP)} '
        f'{_a(links["archeo"], "Conhecer o Archeo")}</p></div></section>'
    )


def _render_html(e: _Entrega, plataforma: str) -> str:
    meta = '<span class="eyebrow">REGIME REGULAR</span>'
    if e.linha_operacao:
        meta += f'<span class="para">{esc(e.linha_operacao)}</span>'
    valores = {
        "TITLE": esc(SKILL_TITLE),
        "FONT_FACE": _font_face(),
        "BASE_CSS": brand.base_css(),
        "LOGO": brand.logo_pair_html(),
        "HEADER_META": meta,
        "FAIXA": f'<div class="faixa" role="note">{esc(FAIXA_EXEMPLO)}</div>' if e.exemplo else "",
        "HERO": _html_hero(e) + _html_observacoes(e),
        "SIMULADO": _html_simulado(e),
        "CENARIO": _html_cenario(e),
        "ITENS": _html_itens(e),
        "MEMORIA": _html_memoria(e),
        "AVISOS_OPERACAO": _html_avisos_operacao(e),
        "SELO": _html_selo(e),
        "AVISOS_FIXOS": _html_avisos_fixos(),
        "COMMERCIAL": _html_comercial(_links(plataforma)),
        "FOOTER": brand.footer_html(REGRA, e.data_do_rodape, "auto"),
    }
    return _preencher(_template(), valores)


# ------------------------------------------------------------------ texto (Markdown)
# Os textos de fora já chegam limpos pelo `_txt_md` (ver _Entrega): aqui só se monta.

def _md_motivos(motivos: list) -> list:
    linhas = []
    for m in motivos:
        cabeca = " ".join(p for p in (f"**{m['titulo']}**" if m["titulo"] else "",
                                      f"({m['codigo']})" if m["codigo"] else "") if p)
        linhas.append("- " + (f"{cabeca}: {m['texto']}" if cabeca and m["texto"] else cabeca or m["texto"]))
    return linhas


def _render_texto(e: _Entrega, plataforma: str) -> str:
    links = _links(plataforma)
    linhas = [f"# {SKILL_TITLE}", ""]
    if e.linha_operacao:
        linhas += [e.linha_operacao, ""]
    if e.exemplo:
        linhas += [f"> {FAIXA_EXEMPLO}", ""]
    if e.nivel_operacao:
        linhas += [f"## {e.titulo_erro_operacao}", ""]
        for erro in e.erros_operacao:
            cabeca = " ".join(p for p in (f"**{erro['titulo']}**" if erro["titulo"] else "",
                                          f"({erro['codigo']})" if erro["codigo"] else "") if p)
            linhas.append("- " + (f"{cabeca}: {erro['texto']}" if cabeca and erro["texto"] else cabeca or erro["texto"]))
        linhas.append("")
    elif not e.tem_total:
        linhas += [f"## {e.sem_total}", "", f"{e.contagem}.", ""]
    else:
        t = e.total
        linhas += ["## Total da operação", "", f"- **CBS:** {_brl(t['cbs'])}", f"- **IBS:** {_brl(t['ibs'])}",
                   f"- **Imposto Seletivo (IS):** {_brl(t['is'])}"]
        linhas += [f"- {nome}: {valor}" for nome, valor in _extras_do_total(e)]
        linhas += [f"- {e.contagem}", ""]
    if e.observacoes:
        linhas += [f"## {TITULO_OBSERVACOES}", ""] + [f"- {o}" for o in e.observacoes] + [""]
    if e.simulado:
        linhas += [f"**{AVISO_SIMULADO}**", ""]
    if not e.nivel_operacao:
        linhas += ["## Alíquotas usadas", ""]
        if e.cenario:
            linhas.append(f"- {e.linha_cenario}.")
            if e.cenario["hipotese"]:
                linhas.append(f"- **{HIPOTESE_CD2}**")
            linhas += [f"- Fonte: {f}" for f in e.cenario["fontes"]]
        else:
            linhas.append(f"- {ALIQUOTAS_INFORMADAS}")
        linhas.append("")
    if e.itens:
        linhas += ["## Itens da operação", "",
                   "| Item | Situação | Base | Alíquota CBS | CBS | Alíquota IBS (estadual + municipal) | IBS | IS |",
                   "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
        for i in e.itens:
            nome = i["numero"] + (f" ({i['descricao']})" if i["descricao"] else "")
            linhas.append("| " + " | ".join([
                nome, i["rotulo"], _valor(_brl, i["base"]), _valor(_pct2, i["p_cbs"]), _valor(_brl, i["v_cbs"]),
                _ibs_pct(i), _valor(_brl, i["v_ibs"]), _valor(_brl, i["v_is"])]) + " |")
        if e.tem_total:
            t = e.total
            linhas.append(f"| **Total** |  | {_valor(_brl, t['base'])} |  | {_brl(t['cbs'])} |  | {_brl(t['ibs'])} "
                          f"| {_brl(t['is'])} |")
        linhas.append("")
        for i in e.itens:
            extra = []
            if i["sugestao"]:
                extra.append(f"- {AVISO_SUGESTAO} {_linha_sugestao(i['sugestao'])}")
                if i["sugestao"]["is_texto"]:
                    extra.append(f"- **Imposto Seletivo:** {i['sugestao']['is_texto']}")
            if i["is_detalhe"]:
                extra.append(f"- **Imposto Seletivo:** {i['is_detalhe']}.")
            extra += _md_motivos(i["motivos"])
            if extra:
                linhas += [f"**Item {i['numero']}**", ""] + extra + [""]
        memoria = [i for i in e.calculados if i["memoria"] or i["memoria_is"]]
        if memoria:
            linhas += ["## Memória de cálculo", ""]
            for i in memoria:
                linhas.append(f"**Item {i['numero']}**")
                linhas += [f"- {nome}: {texto}" for nome, texto in i["memoria"]]
                if i["memoria_is"]:
                    linhas.append(f"- Imposto Seletivo: {i['memoria_is']}")
                linhas.append("")
    if e.avisos:
        linhas += ["## Avisos da operação", ""] + [f"- {a}" for a in e.avisos] + [""]
    if e.normalizacoes:
        linhas += ["## O que arrumamos na entrada", ""] + [f"- {n}" for n in e.normalizacoes] + [""]
    selo = [t for t in (e.selo or e.versao_sem_selo, e.selo_extra) if t]
    if selo:
        linhas += ["## Selo de conferência" if e.selo else "## Versão da calculadora", ""] + selo + [""]
    linhas += [f"**{AVISO_FIXO}**", "",
               f"Fonte do código e das tabelas: [repositório da calculadora da Receita Federal]({RECEITA_URL}).", "",
               f"## {TITULO_CONVITE}", "", f"{TEXTO_CONVITE} {links['calc']}", "",
               f"## {TITULO_COMERCIAL}", "", TEXTO_COMERCIAL, "",
               f"- Fale com a CD2 no WhatsApp: {links['whatsapp']}", f"- Conheça a CD2: {links['cd2']}",
               f"- {TITULO_ERP} {TEXTO_ERP} {links['archeo']}", "", "---", "",
               brand.footer_text(REGRA, e.data_do_rodape)]
    return "\n".join(linhas) + "\n"


# ------------------------------------------------------------------ WhatsApp

def _whatsapp_partes(e: _Entrega, nivel: int, links: dict) -> list:
    """Partes do resumo como (texto, obrigatória). Quanto maior o nível, mais enxuto. Os campos variáveis
    já chegam limpos e aqui são cortados. Obrigatórios: totais, aviso de simulado, cenário, fonte, hipótese,
    selo, aviso de que não é da Receita e o convite com o link."""
    partes = [(f"*{SKILL_TITLE}*", True)]
    if e.exemplo:
        partes.append((FAIXA_EXEMPLO if nivel < 2 else "Exemplo ilustrativo. Não é a conta da sua empresa.", True))
    if e.linha_operacao:
        partes.append((_corta(e.linha_operacao, 60), False))
    if e.nivel_operacao:
        partes.append((_corta(e.titulo_erro_operacao, 70) + ".", True))
        for n, erro in enumerate(e.erros_operacao[:3]):
            cabeca = " ".join(p for p in (_corta(erro["titulo"], 60), f"({erro['codigo']})" if erro["codigo"] else "") if p)
            partes.append((f"- {cabeca}", n == 0))
            if erro["texto"] and n == 0:
                partes.append((_corta(erro["texto"], 150), False))
        if len(e.erros_operacao) > 3:
            partes.append((f"Mais {len(e.erros_operacao) - 3} erros na página completa.", False))
    elif not e.tem_total:
        partes += [(e.sem_total + ".", True), (_corta(e.contagem, 80) + ".", False)]
    else:
        t = e.total
        partes += [(f"CBS: {_brl(t['cbs'])}", True), (f"IBS: {_brl(t['ibs'])}", True),
                   (f"Imposto Seletivo: {_brl(t['is'])}", True), (_corta(e.contagem, 80) + ".", False)]
    partes += [(o, True) for o in _whatsapp_observacoes(e, nivel)]
    if not e.nivel_operacao:
        for i in e.problemas[:2]:
            codigos = ", ".join(m["codigo"] for m in i["motivos"] if m["codigo"])
            partes.append((_corta(f"- Item {i['numero']}: {i['rotulo']}" + (f" ({_corta(codigos, 30)})" if codigos else ""), 90), True))
    if e.simulado:
        partes.append((AVISO_SIMULADO_CURTO if nivel else AVISO_SIMULADO, True))
    if not e.nivel_operacao:
        if e.cenario:
            dominios = []
            for f in e.cenario["fontes"]:
                if _dominio(f) and _dominio(f) not in dominios:
                    dominios.append(_dominio(f))
            texto = _corta(e.linha_cenario, 150) + "."
            if dominios:
                texto += " Fontes: " + ", ".join(dominios) + "."
            partes.append((texto, True))
            if e.cenario["hipotese"]:
                partes.append((HIPOTESE_CD2_CURTA if nivel else HIPOTESE_CD2, True))
        else:
            partes.append((ALIQUOTAS_INFORMADAS, True))
    if e.alguma_sugestao:
        partes.append((AVISO_SUGESTAO, True))
    selo = e.selo if nivel < 2 else e.selo_curto
    if selo:
        partes.append((selo, True))
    elif e.versao_sem_selo:
        partes.append((e.versao_sem_selo, False))
    partes.append((AVISO_FIXO if nivel == 0 else AVISO_FIXO_CURTO, True))
    partes.append((f"Veja a loja inteira na calc-imposto: {links['calc']}", True))
    partes.append((brand.footer_text(REGRA, e.data_do_rodape) if nivel == 0 else "CD2 Tech", nivel == 0))
    return partes


def _whatsapp_observacoes(e: _Entrega, nivel: int) -> list:
    """Observações no resumo: quanto maior o nível, mais curtas; nos níveis apertados fica só a primeira,
    cortada, com o aviso de que há mais na página. A primeira nunca some."""
    obs = e.observacoes
    if not obs:
        return []
    if nivel == 0:
        return list(obs)
    if nivel == 1:
        return [_corta(o, 150) for o in obs]
    primeira = _corta(obs[0], 150 if nivel == 2 else 60)
    return [primeira] + ([MAIS_OBSERVACOES] if len(obs) > 1 else [])


def _juntar(partes: list) -> str:
    return "\n".join(texto for texto, _ in partes) + "\n"


def _render_whatsapp(e: _Entrega, plataforma: str) -> str:
    """Resumo curto, sempre com no máximo 1000 caracteres. Se não couber, cai a última parte opcional;
    se ainda não couber, o resumo fica mais enxuto, nível a nível."""
    links = _links(plataforma)
    for nivel in range(4):
        partes = _whatsapp_partes(e, nivel, links)
        while len(_juntar(partes)) > LIMITE_WHATSAPP:
            opcionais = [n for n, (_, obrigatoria) in enumerate(partes) if not obrigatoria]
            if not opcionais:
                break
            partes.pop(opcionais[-1])
        if len(_juntar(partes)) <= LIMITE_WHATSAPP:
            return _juntar(partes)
    # Último recurso: só o essencial, com as observações e o selo no mínimo.
    essencial = [(f"*{SKILL_TITLE}*", True)]
    if e.nivel_operacao:
        erro = e.erros_operacao[0]
        essencial.append((_corta(f"{erro['titulo']} ({erro['codigo']})" if erro["codigo"] else erro["titulo"], 80), True))
    elif e.tem_total:
        essencial += [(f"CBS: {_brl(e.total['cbs'])}", True), (f"IBS: {_brl(e.total['ibs'])}", True),
                      (f"Imposto Seletivo: {_brl(e.total['is'])}", True)]
    else:
        essencial.append((e.sem_total + ".", True))
    essencial += [(o, True) for o in _whatsapp_observacoes(e, 3)]
    if e.simulado:
        essencial.append((AVISO_SIMULADO_CURTO, True))
    if e.cenario and not e.nivel_operacao:
        dominios = sorted({_dominio(f) for f in e.cenario["fontes"] if _dominio(f)})
        essencial.append((f"Cenário {e.cenario['nome']}" + (f", {e.cenario['conferido']}" if e.cenario["conferido"] else "")
                          + (f". Fontes: {', '.join(dominios)}." if dominios else "."), True))
        if e.cenario["hipotese"]:
            essencial.append((HIPOTESE_CD2_CURTA, True))
    essencial += [(e.selo_curto, True)] if e.selo_curto else []
    essencial += [(AVISO_FIXO_CURTO, True), (f"Calc-imposto: {links['calc']}", True), ("CD2 Tech", True)]
    return _juntar(essencial)


# ------------------------------------------------------------------ API e linha de comando

def render(entrega: dict, formato: str, plataforma: str = "Claude", conferencia_da_base: bool = False) -> str:
    """Monta a entrega no formato pedido. `entrega` segue o formato do cabeçalho deste arquivo.
    Com `conferencia_da_base`, a conferência (N de N casos) que faltar na entrega é lida da base local."""
    if formato not in FORMATOS:
        raise ValueError(f"formato desconhecido: {formato} (use {', '.join(FORMATOS)})")
    if not isinstance(entrega, dict) or not isinstance(entrega.get("calculo"), dict) \
            or "corpo" not in entrega["calculo"]:
        raise ValueError('a entrega precisa trazer "calculo" com a saída do calc.py (status, corpo, versao)')
    modelo = _Entrega(entrega, formato, conferencia_da_base)
    plataforma = _txt(plataforma) or "Claude"
    return {"html": _render_html, "texto": _render_texto, "whatsapp": _render_whatsapp}[formato](modelo, plataforma)


def _falhar(erros) -> int:
    print(json.dumps({"errors": erros}, ensure_ascii=False, indent=2))
    return 2


def _ler_json(caminho):
    try:
        if caminho is None:
            bruto = sys.stdin.read()
        else:
            with open(caminho, encoding="utf-8") as arquivo:
                bruto = arquivo.read()
    except OSError as exc:
        raise ValueError(f"Não consegui ler a entrega: {exc.strerror or exc}.")
    try:
        return json.loads(bruto)
    except ValueError as exc:
        raise ValueError(f"JSON inválido na entrega: {exc}.")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Entrega da skill cbs-ibs-regime-regular (CD2 Tech).")
    parser.add_argument("entrega", nargs="?", help="entrega.json (padrão: entrada padrão)")
    parser.add_argument("--formato", required=True, choices=FORMATOS)
    parser.add_argument("--out", help="grava a entrega neste arquivo em vez de imprimir")
    parser.add_argument("--plataforma", default="Claude", help="nome da plataforma na mensagem do WhatsApp")
    args = parser.parse_args(argv)
    for fluxo in (sys.stdin, sys.stdout):
        if hasattr(fluxo, "reconfigure"):
            fluxo.reconfigure(encoding="utf-8")
    if args.entrega is None and sys.stdin.isatty():
        return _falhar(["Informe a entrega com render.py entrega.json, ou mande o JSON pela entrada padrão."])
    try:
        dados = _ler_json(args.entrega)
        texto = render(dados, args.formato, args.plataforma, conferencia_da_base=True)
    except ValueError as exc:
        return _falhar([str(exc)])
    except Exception as exc:  # rede de segurança: JSON limpo no stdout, detalhe no stderr, sem traceback
        print(f"render.py: {type(exc).__name__}: {exc}", file=sys.stderr)
        return _falhar([f"Erro inesperado ao montar a entrega: {type(exc).__name__}."])
    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="") as arquivo:
            arquivo.write(texto)
    else:
        sys.stdout.write(texto)
    return 0


if __name__ == "__main__":
    sys.exit(main())
