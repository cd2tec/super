#!/usr/bin/env python3
"""Entrega da skill cbs-ibs-simples: monta a página, o Markdown e o resumo de WhatsApp
a partir da saída do calc.py.

Os números vêm todos do corpo oficial. Aqui só se formata, escapa e organiza: valor em
reais com vírgula, alíquota em porcentagem, estado em português. O JSON nunca é alterado.

Uso:
    python3 render.py --input entrega.json --formato html --out conta.html
    python3 render.py --input entrega.json --formato whatsapp

`entrega.json` = {"operacao": {...}, "calculo": {...saída do calc.py...},
                  "sugestoes": {"<número do item>": {...}} | null, "exemplo": bool,
                  "aviso_versao": {...} | null}

Formatos: html, markdown, whatsapp. Só biblioteca padrão (Python 3.10+).
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
import simples_motor  # noqa: E402
from brand import esc  # noqa: E402

SKILL = "cbs-ibs-simples"
SKILL_TITLE = "CBS e IBS no Simples Nacional"
REGRA = "Simples Nacional"
FORMATOS = ("html", "markdown", "whatsapp")
CD2_URL = "https://cd2.com.br/"
ARCHEO_URL = "https://archeo.cd2.com.br/"
RECEITA_URL = "https://github.com/ReceitaFederal/calculadora-rtc-simples-nacional"
INICIO_DAS_REGRAS = 2027
LIMITE_WHATSAPP = 1000

ESTADOS = {
    "CALCULADO": ("Calculado", "green"),
    "NAO_CALCULADO": ("Não calculado", "amber"),
    "NAO_IMPLEMENTADO": ("Ainda não tratado pela calculadora oficial", "amber"),
    "INCONSISTENCIA_ENTRADA": ("Dado de entrada com problema", "red"),
    "FALHA_TECNICA": ("Falha técnica", "red"),
}

AVISO_FIXO = (
    "Resultado gerencial. Esta skill foi feita pela CD2 Tech com o código e as tabelas da calculadora "
    "oficial e não é um aplicativo da Receita Federal. Confira com o seu contador."
)
AVISO_SUGESTAO = "Classificação sugerida pela CD2, confirme com o contador."
SELO_SUGESTAO = "O selo vale para a conta, não para a classificação sugerida."
FAIXA_EXEMPLO = "Exemplo ilustrativo com alíquotas da documentação oficial. Não é a conta da sua empresa."
TEXTO_VSN015_DATA = (
    "As regras do Simples Nacional para a CBS e o IBS começam em 01/01/2027. "
    "Refaça a conta com uma data a partir daí."
)
SEM_ITEM = "Nenhum item foi calculado."
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


_LIMPADORES = {"html": _txt, "markdown": _txt_md, "whatsapp": _txt_wa}


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


def _brl(valor) -> str:
    d = _dec(valor)
    if d is None:
        return _corta(_txt(valor), 30) or "s/d"
    d = _arredondar(d, 2)
    inteiro, frac = f"{abs(d):.2f}".split(".")
    sinal = "-" if d < 0 else ""
    return f"{sinal}R$ {_milhar(inteiro)},{frac}"


def _pct(valor) -> str:
    d = _dec(valor)
    if d is None:
        return _corta(_txt(valor), 30) or "s/d"
    d = _arredondar(d, 4)
    inteiro, frac = f"{abs(d):.4f}".split(".")
    frac = frac.rstrip("0").ljust(2, "0")
    sinal = "-" if d < 0 else ""
    return f"{sinal}{_milhar(inteiro)},{frac}%"


def _inteiro_ou_decimal(valor) -> str:
    """Número curto sem zeros à direita, com vírgula (por exemplo, 60 ou 12,5). Vazio se não for número."""
    d = _dec(valor)
    if d is None or abs(d) > Decimal("100000"):
        return ""
    texto = f"{_arredondar(d, 2):f}"
    if "." in texto:
        texto = texto.rstrip("0").rstrip(".")
    return texto.replace(".", ",")


_DATA_ISO = re.compile(r"\d{4}-\d{2}-\d{2}")


def _data(texto) -> str:
    texto = _txt(texto)
    if _DATA_ISO.match(texto):
        return f"{texto[8:10]}/{texto[5:7]}/{texto[:4]}"
    return texto


def _ano_da_data(texto):
    texto = _txt(texto)
    achou = re.match(r"^(\d{4})-\d{2}-\d{2}", texto) or re.match(r"^\d{2}/\d{2}/(\d{4})", texto)
    return int(achou.group(1)) if achou else None


def _corta(texto: str, limite: int) -> str:
    return texto if len(texto) <= limite else texto[: limite - 3].rstrip() + "..."


def _titulo_e_codigo(titulo: str, codigo: str, limite: int) -> str:
    """Corta o título, nunca o código."""
    codigo = _corta(codigo, 20)
    if not codigo:
        return _corta(titulo, limite)
    return f"{_corta(titulo, max(limite - len(codigo) - 3, 10))} ({codigo})".strip()


def _dict(valor) -> dict:
    return valor if isinstance(valor, dict) else {}


def _lista(valor) -> list:
    return valor if isinstance(valor, list) else []


_VERSAO = re.compile(r"[vV]?\d+(?:\.\d+)*")


# ------------------------------------------------------------------ leitura da entrega

class _Entrega:
    """Tudo o que a entrega precisa, lido uma vez. Todo texto que vem de fora passa por `self.t`, o
    limpador do formato: o que sai daqui já é seguro para o formato (o HTML ainda escapa na saída)."""

    def __init__(self, dados: dict, formato: str):
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
        self.normalizacoes = [t for t in (self.t(n) for n in _lista(calculo.get("normalizacoes"))) if t]
        self.operacao = _dict(dados.get("operacao"))
        self.exemplo = bool(dados.get("exemplo"))
        sugestoes = dados.get("sugestoes")
        self.sugestoes = sugestoes if isinstance(sugestoes, dict) else {}
        aviso = _dict(dados.get("aviso_versao"))
        self.versao_nova = (self._versao(aviso.get("calculadora"))
                            if aviso.get("consultado") is True and aviso.get("ha_versao_nova") is True else "")

        oper = _dict(self.corpo.get("oper"))
        data = _txt(oper.get("data") or self.operacao.get("data"))
        self.data_iso = data
        self.op_id = self.t(self.operacao.get("id"))
        self.antes_de_2027 = (_ano_da_data(data) or INICIO_DAS_REGRAS) < INICIO_DAS_REGRAS

        self.nivel_operacao = not isinstance(self.corpo.get("objetos"), list)
        self.erros_operacao = self._erros_da_operacao() if self.nivel_operacao else []
        self.itens = [] if self.nivel_operacao else self._itens()
        self.calculados = [i for i in self.itens if i["estado"] == "CALCULADO"]
        self.problemas = [i for i in self.itens if i["estado"] != "CALCULADO"]
        self.avisos = self._avisos()
        total = _dict(self.corpo.get("total"))
        self.total = {k: self.num(total.get(k)) for k in
                      ("vBC", "vCBSSNtot", "vIBSSNtot", "totalTributos", "vCBSSNtotPendSusp",
                       "vIBSSNtotPendSusp", "totalIs")}
        self.tem_total = bool(self.calculados) and _dec(self.total["vCBSSNtot"]) is not None \
            and _dec(self.total["vIBSSNtot"]) is not None
        self.alguma_sugestao = any(i["sugestao"] for i in self.itens) or (bool(self.sugestoes) and self.nivel_operacao)
        self.itens_com_is = [i["numero"] for i in self.itens if i["sugestao"] and i["sugestao"]["is_texto"]]

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

    # --- erros da operação (400, 422, 500...)
    def _erros_da_operacao(self) -> list:
        erros = []
        for e in _lista(self.corpo.get("errors")):
            if not isinstance(e, dict):
                continue
            codigo = self.t(e.get("code"))
            erros.append({"titulo": self.t(e.get("title")), "codigo": codigo,
                          "texto": self._texto_do_erro(codigo, self.t(e.get("detail")))})
        if not erros:
            titulo = self.t(self.corpo.get("title")) or "A calculadora oficial não fez a conta"
            erros.append({"titulo": titulo, "codigo": "", "texto": self.t(self.corpo.get("detail"))})
        return erros

    def _texto_do_erro(self, codigo: str, detalhe: str) -> str:
        if codigo == "VSN-015" and self.antes_de_2027:
            return TEXTO_VSN015_DATA
        return detalhe

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
            origem = self._item_da_operacao(posicao, numero)
            descricao = ""
            for campo in ("descricao", "nome", "produto", "xProd"):
                if self.t(origem.get(campo)):
                    descricao = self.t(origem.get(campo))
                    break
            sugestao = self.sugestoes.get(numero)
            memoria = _dict(trib.get("memoriaCalculo"))
            base = trib.get("vBC") if trib.get("vBC") is not None else trib.get("vRBSNitem")
            saida.append({
                "numero": numero, "estado": estado, "rotulo": rotulo, "cor": cor, "descricao": descricao,
                "base": self.num(base), "p_cbs": self.num(trib.get("pCBSSN")), "p_ibs": self.num(trib.get("pIBSSN")),
                "v_cbs": self.num(trib.get("vCBSSN")), "v_ibs": self.num(trib.get("vIBSSN")),
                "total": self.num(trib.get("totalItem")),
                "sugestao": self._sugestao(sugestao) if isinstance(sugestao, dict) else None,
                "motivos": self._motivos(obj),
                "memoria": {k: self.t(memoria.get(k)) for k in ("cbs", "ibs") if self.t(memoria.get(k))},
            })
        return saida

    def _sugestao(self, s: dict) -> dict:
        imposto = _dict(s.get("imposto_seletivo"))
        return {"classificacao": self.t(s.get("cClassTrib")), "cst": self.t(s.get("cst")),
                "descricao": self.t(s.get("descricao")), "reducao": _inteiro_ou_decimal(s.get("reducao")),
                "is_texto": self.t(imposto.get("texto")) if imposto.get("na_lista") is True else ""}

    def _motivos(self, obj: dict) -> list:
        motivos = []
        estado = _dict(obj.get("motivoEstado"))
        if estado:
            motivos.append({"titulo": "", "codigo": self.t(estado.get("codigo")),
                            "texto": self.t(estado.get("descricao")), "detalhe": ""})
        for e in _lista(obj.get("erros")):
            if not isinstance(e, dict):
                continue
            codigo = self.t(e.get("code"))
            oficial = simples_motor.CODIGOS.get(_txt(e.get("code")))
            detalhe = self.t(e.get("detail"))
            if codigo == "VSN-015" and self.antes_de_2027:
                texto, detalhe = TEXTO_VSN015_DATA, ""
            elif oficial:
                texto = self.t(oficial["descricao"])
                detalhe = "" if detalhe == texto else detalhe
            else:
                texto, detalhe = detalhe, ""
            motivos.append({"titulo": self.t(e.get("title")), "codigo": codigo, "texto": texto, "detalhe": detalhe})
        return motivos

    def _avisos(self) -> list:
        avisos = [t for t in (self.t(a) for a in _lista(self.corpo.get("avisos"))) if t]
        resolucao = _dict(self.corpo.get("resolucaoAliquotas"))
        avisos += [t for t in (self.t(a) for a in _lista(resolucao.get("avisos"))) if t]
        for item in _lista(resolucao.get("itens")):
            if isinstance(item, dict):
                avisos += [f"Item {self.t(item.get('numero'))}: {t}"
                           for t in (self.t(a) for a in _lista(item.get("avisos"))) if t]
        return avisos

    # --- selo, versão e rodapé
    def _selo(self, com_data: bool) -> str:
        if not self.v_calculadora and not self.v_base:
            return ""
        partes = [f"versão {self.v_calculadora}" if self.v_calculadora else "",
                  f"base {self.v_base}" if self.v_base else ""]
        texto = "Conta igual à da calculadora oficial do Simples Nacional " + ", ".join(p for p in partes if p)
        if com_data and self.conferida:
            texto += f", conferida em {_data(self.conferida)}"
        return texto + "."

    @property
    def selo(self) -> str:
        return self._selo(True)

    @property
    def selo_curto(self) -> str:
        return self._selo(False)

    @property
    def selo_extra(self) -> str:
        return SELO_SUGESTAO if self.alguma_sugestao else ""

    @property
    def linha_versao_nova(self) -> str:
        if self.versao_nova:
            return f"A Receita já publicou a versão {self.versao_nova}. Atualize o plugin para a conta mais recente."
        return ""

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


def _linha_sugestao(s: dict) -> str:
    texto = f"cClassTrib {s['classificacao']}" if s["classificacao"] else "classificação"
    if s["cst"]:
        texto += f" (CST {s['cst']})"
    if s["reducao"]:
        texto += f", redução de {s['reducao']}%"
    return texto + (f": {s['descricao']}" if s["descricao"] else "")


def _extras_do_total(e: _Entrega) -> list:
    """Linhas de apoio dos totais: total de tributos, valores pendentes ou suspensos e Imposto Seletivo."""
    t = e.total
    linhas = []
    if _dec(t.get("totalTributos")) is not None:
        linhas.append(("Total de tributos", _brl(t["totalTributos"])))
    for chave, nome in (("vCBSSNtotPendSusp", "CBS pendente ou suspensa"), ("vIBSSNtotPendSusp", "IBS pendente ou suspenso"),
                        ("totalIs", "Imposto Seletivo")):
        d = _dec(t.get(chave))
        if d is not None and d != 0:
            linhas.append((nome, _brl(t[chave])))
    return linhas


# ------------------------------------------------------------------ links

def _links(plataforma: str) -> dict:
    return {
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
        cabeca = ""
        if m["titulo"]:
            cabeca += f"<b>{esc(m['titulo'])}</b> "
        if m["codigo"]:
            cabeca += f'<span class="cod">{esc(m["codigo"])}</span>'
        corpo = f"<span>{esc(m['texto'])}</span>" if m["texto"] else ""
        detalhe = f'<span class="mut">{esc(m["detalhe"])}</span>' if m["detalhe"] else ""
        itens.append(f"<li>{('<span>' + cabeca + '</span>') if cabeca else ''}{corpo}{detalhe}</li>")
    return f'<ul class="motivos">{"".join(itens)}</ul>'


def _celula(rotulo: str, valor, formatar) -> str:
    texto = esc(formatar(valor)) if valor is not None else ""
    return f'<td class="n" data-label="{rotulo}">{texto}</td>'


def _html_linha_item(i: dict) -> str:
    extra = ""
    if i["sugestao"]:
        extra += (f'<span><span class="sugerida">{esc(AVISO_SUGESTAO)}</span> '
                  f'{esc(_linha_sugestao(i["sugestao"]))}</span>')
        if i["sugestao"]["is_texto"]:
            extra += f'<span><b>Imposto Seletivo:</b> {esc(i["sugestao"]["is_texto"])}</span>'
    extra += _html_motivos(i["motivos"])
    desc = f'<span class="item-desc">{esc(i["descricao"])}</span>' if i["descricao"] else ""
    extra_html = f'<div class="item-extra">{extra}</div>' if extra else ""
    return (
        f'<tr><th scope="row">Item {esc(i["numero"])}{desc}{extra_html}</th>'
        f'<td data-label="Situação"><span class="pill {i["cor"]}">{esc(i["rotulo"])}</span></td>'
        + _celula("Base", i["base"], _brl) + _celula("Alíquota CBS", i["p_cbs"], _pct)
        + _celula("Alíquota IBS", i["p_ibs"], _pct) + _celula("CBS", i["v_cbs"], _brl)
        + _celula("IBS", i["v_ibs"], _brl) + "</tr>"
    )


def _html_itens(e: _Entrega) -> str:
    if not e.itens:
        return ""
    linhas = "".join(_html_linha_item(i) for i in e.itens)
    rodape = ""
    if e.tem_total:
        t = e.total
        base = _brl(t["vBC"]) if _dec(t.get("vBC")) is not None else ""
        rodape = (
            '<tfoot><tr><th scope="row">Total</th><td data-label="Situação"></td>'
            f'<td class="n" data-label="Base">{esc(base)}</td><td data-label="Alíquota CBS"></td>'
            f'<td data-label="Alíquota IBS"></td><td class="n" data-label="CBS">{esc(_brl(t["vCBSSNtot"]))}</td>'
            f'<td class="n" data-label="IBS">{esc(_brl(t["vIBSSNtot"]))}</td></tr></tfoot>'
        )
    return (
        '<section class="secao" aria-labelledby="t-itens"><h2 class="heading-md" id="t-itens">Itens da operação</h2>'
        '<table class="tabela"><thead><tr><th scope="col">Item</th><th scope="col">Situação</th>'
        '<th scope="col" class="n">Base</th><th scope="col" class="n">Alíquota CBS</th>'
        '<th scope="col" class="n">Alíquota IBS</th><th scope="col" class="n">CBS</th>'
        '<th scope="col" class="n">IBS</th></tr></thead>'
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
            '<section class="bloco b-raised hero" aria-labelledby="t-total"><p class="eyebrow mut">CBS E IBS</p>'
            f'<h1 class="display-lg" id="t-total">{esc(e.sem_total)}</h1>'
            f'<p class="stat-line">{esc(e.contagem)}. Veja abaixo o detalhe de cada item.</p></section>'
        )
    t = e.total
    extras = "".join(f'<p class="stat-line">{esc(nome)}: <b class="num">{esc(valor)}</b></p>'
                     for nome, valor in _extras_do_total(e))
    return (
        '<section class="bloco b-brand hero" aria-labelledby="t-total"><p class="eyebrow">TOTAL DA OPERAÇÃO</p>'
        '<h1 class="display-lg" id="t-total">CBS e IBS do Simples Nacional</h1>'
        '<div class="hero-numeros">'
        f'<div><p class="stat-line">CBS</p><p class="stat">{esc(_brl(t["vCBSSNtot"]))}</p></div>'
        f'<div><p class="stat-line">IBS</p><p class="stat">{esc(_brl(t["vIBSSNtot"]))}</p></div></div>'
        f'<div class="hero-extra">{extras}<p class="stat-line">{esc(e.contagem)}</p></div></section>'
    )


def _html_memoria(e: _Entrega) -> str:
    blocos = []
    for i in e.calculados:
        if i["memoria"]:
            partes = "".join(f"<p><b>{nome}:</b> {esc(i['memoria'][chave])}</p>"
                             for chave, nome in (("cbs", "CBS"), ("ibs", "IBS")) if chave in i["memoria"])
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
                  '<p class="legenda">Antes de calcular, ajustamos estes pontos para o formato que a calculadora oficial pede.</p>'
                  f'<ul class="lista">{itens}</ul></section>')
    return saida


def _html_selo(e: _Entrega) -> str:
    partes = [t for t in (e.selo, e.selo_extra, e.linha_versao_nova) if t]
    if not partes:
        return ""
    paragrafos = "".join(f"<p>{esc(t)}</p>" for t in partes)
    return f'<section class="selo" aria-label="Selo de conferência"><p class="eyebrow">CONTA CONFERIDA</p>{paragrafos}</section>'


def _html_avisos_fixos() -> str:
    return (
        f'<div class="aviso"><p><b>{esc(AVISO_FIXO)}</b></p></div>'
        f'<p class="legenda">Fonte do código e das tabelas: {_a(RECEITA_URL, "repositório da calculadora da Receita Federal")}.</p>'
    )


def _html_comercial(links: dict) -> str:
    return (
        '<section class="bloco b-raised comercial" aria-labelledby="t-comercial">'
        f'<h2 class="heading-md" id="t-comercial">{esc(TITULO_COMERCIAL)}</h2><p>{esc(TEXTO_COMERCIAL)}</p>'
        f'<div class="botoes">{_a(links["whatsapp"], "Falar com a CD2 no WhatsApp", "cd2-botao")}'
        f'{_a(links["cd2"], "Conhecer a CD2", "cd2-botao sec")}</div>'
        f'<div class="extras"><p><b>{esc(TITULO_ERP)}</b> {esc(TEXTO_ERP)} '
        f'{_a(links["archeo"], "Conhecer o Archeo")}</p></div></section>'
    )


def _render_html(e: _Entrega, plataforma: str) -> str:
    meta = '<span class="eyebrow">SIMPLES NACIONAL</span>'
    if e.linha_operacao:
        meta += f'<span class="para">{esc(e.linha_operacao)}</span>'
    valores = {
        "TITLE": esc(SKILL_TITLE),
        "FONT_FACE": _font_face(),
        "BASE_CSS": brand.base_css(),
        "LOGO": brand.logo_pair_html(),
        "HEADER_META": meta,
        "FAIXA": f'<div class="faixa" role="note">{esc(FAIXA_EXEMPLO)}</div>' if e.exemplo else "",
        "HERO": _html_hero(e),
        "ITENS": _html_itens(e),
        "MEMORIA": _html_memoria(e),
        "AVISOS_OPERACAO": _html_avisos_operacao(e),
        "SELO": _html_selo(e),
        "AVISOS_FIXOS": _html_avisos_fixos(),
        "COMMERCIAL": _html_comercial(_links(plataforma)),
        "FOOTER": brand.footer_html(REGRA, e.data_do_rodape, "auto"),
    }
    return _preencher(_template(), valores)


# ------------------------------------------------------------------ Markdown
# Os textos de fora já chegam limpos pelo `_txt_md` (ver _Entrega): aqui só se monta.

def _md_motivos(motivos: list) -> list:
    linhas = []
    for m in motivos:
        cabeca = " ".join(p for p in (f"**{m['titulo']}**" if m["titulo"] else "",
                                      f"({m['codigo']})" if m["codigo"] else "") if p)
        corpo = " ".join(p for p in (m["texto"], m["detalhe"]) if p)
        linhas.append("- " + (f"{cabeca}: {corpo}" if cabeca and corpo else cabeca or corpo))
    return linhas


def _render_markdown(e: _Entrega, plataforma: str) -> str:
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
        linhas += ["## CBS e IBS do Simples Nacional", "", f"- **CBS:** {_brl(t['vCBSSNtot'])}",
                   f"- **IBS:** {_brl(t['vIBSSNtot'])}"]
        linhas += [f"- {nome}: {valor}" for nome, valor in _extras_do_total(e)]
        linhas += [f"- {e.contagem}", ""]
    if e.itens:
        linhas += ["## Itens da operação", "", "| Item | Situação | Base | Alíquota CBS | Alíquota IBS | CBS | IBS |",
                   "| --- | --- | ---: | ---: | ---: | ---: | ---: |"]
        for i in e.itens:
            nome = i["numero"] + (f" ({i['descricao']})" if i["descricao"] else "")
            valores = [_brl(i["base"]) if i["base"] is not None else "",
                       _pct(i["p_cbs"]) if i["p_cbs"] is not None else "",
                       _pct(i["p_ibs"]) if i["p_ibs"] is not None else "",
                       _brl(i["v_cbs"]) if i["v_cbs"] is not None else "",
                       _brl(i["v_ibs"]) if i["v_ibs"] is not None else ""]
            linhas.append("| " + " | ".join([nome, i["rotulo"]] + valores) + " |")
        if e.tem_total:
            t = e.total
            base = _brl(t["vBC"]) if _dec(t.get("vBC")) is not None else ""
            linhas.append(f"| **Total** |  | {base} |  |  | {_brl(t['vCBSSNtot'])} | {_brl(t['vIBSSNtot'])} |")
        linhas.append("")
        for i in e.itens:
            extra = []
            if i["sugestao"]:
                extra.append(f"- {AVISO_SUGESTAO} {_linha_sugestao(i['sugestao'])}")
                if i["sugestao"]["is_texto"]:
                    extra.append(f"- **Imposto Seletivo:** {i['sugestao']['is_texto']}")
            extra += _md_motivos(i["motivos"])
            if extra:
                linhas += [f"**Item {i['numero']}**", ""] + extra + [""]
        memoria = [i for i in e.calculados if i["memoria"]]
        if memoria:
            linhas += ["## Memória de cálculo", ""]
            for i in memoria:
                linhas.append(f"**Item {i['numero']}**")
                linhas += [f"- {nome}: {i['memoria'][chave]}"
                           for chave, nome in (("cbs", "CBS"), ("ibs", "IBS")) if chave in i["memoria"]]
                linhas.append("")
    if e.avisos:
        linhas += ["## Avisos da operação", ""] + [f"- {a}" for a in e.avisos] + [""]
    if e.normalizacoes:
        linhas += ["## O que arrumamos na entrada", ""] + [f"- {n}" for n in e.normalizacoes] + [""]
    selo = [t for t in (e.selo, e.selo_extra, e.linha_versao_nova) if t]
    if selo:
        linhas += ["## Selo de conferência", ""] + selo + [""]
    linhas += [f"**{AVISO_FIXO}**", "", f"Fonte do código e das tabelas: [repositório da calculadora da Receita Federal]({RECEITA_URL}).", "",
               f"## {TITULO_COMERCIAL}", "", TEXTO_COMERCIAL, "",
               f"- Fale com a CD2 no WhatsApp: {links['whatsapp']}", f"- Conheça a CD2: {links['cd2']}",
               f"- {TITULO_ERP} {TEXTO_ERP} {links['archeo']}", "", "---", "",
               brand.footer_text(REGRA, e.data_do_rodape)]
    return "\n".join(linhas) + "\n"


# ------------------------------------------------------------------ WhatsApp

def _whatsapp_partes(e: _Entrega, nivel: int) -> list:
    """Partes do resumo como (texto, obrigatória). Quanto maior o nível, mais enxuto. Os campos variáveis
    já chegam limpos e aqui são cortados."""
    partes = [(f"*{SKILL_TITLE}*", True)]
    if e.exemplo:
        partes.append((FAIXA_EXEMPLO if nivel < 2 else "Exemplo ilustrativo. Não é a conta da sua empresa.", True))
    if e.linha_operacao:
        partes.append((_corta(e.linha_operacao, 60), False))
    limite_titulo = 90 if nivel == 0 else 50
    if e.nivel_operacao:
        partes.append((_corta(e.titulo_erro_operacao, 70) + ".", True))
        for n, erro in enumerate(e.erros_operacao[:3]):
            partes.append((f"- {_titulo_e_codigo(erro['titulo'], erro['codigo'], limite_titulo)}", n == 0))
            if erro["texto"] and n == 0:
                data = erro["codigo"] == "VSN-015" and e.antes_de_2027
                partes.append((_corta(erro["texto"], 150), data and nivel < 3))
        if len(e.erros_operacao) > 3:
            partes.append((f"Mais {len(e.erros_operacao) - 3} erros na página completa.", False))
    elif not e.tem_total:
        partes.append((e.sem_total + ".", True))
        partes.append((_corta(e.contagem, 80) + ".", False))
    else:
        t = e.total
        partes += [(f"CBS: {_brl(t['vCBSSNtot'])}", True), (f"IBS: {_brl(t['vIBSSNtot'])}", True)]
        if _dec(t.get("totalTributos")) is not None:
            partes.append((f"Total: {_brl(t['totalTributos'])}", False))
        partes.append((_corta(e.contagem, 80) + ".", False))
    if not e.nivel_operacao:
        for i in e.problemas[:2]:
            codigos = ", ".join(m["codigo"] for m in i["motivos"] if m["codigo"])
            partes.append((_corta(f"- Item {i['numero']}: {i['rotulo']}" + (f" ({_corta(codigos, 30)})" if codigos else ""), 90), False))
        if e.itens_com_is:
            partes.append((_corta("Imposto Seletivo: veja o item " + ", ".join(e.itens_com_is[:5]) + " na página completa.", 90), False))
    if e.alguma_sugestao:
        partes.append((AVISO_SUGESTAO, True))
    selo = e.selo if nivel < 3 else e.selo_curto
    if selo:
        partes.append((selo + (" " + e.selo_extra if e.selo_extra else ""), True))
    if e.linha_versao_nova:
        partes.append((e.linha_versao_nova, False))
    partes += [(AVISO_FIXO, True), (brand.footer_text(REGRA, e.data_do_rodape), True)]
    return partes


def _juntar(partes: list) -> str:
    return "\n".join(texto for texto, _ in partes) + "\n"


def _render_whatsapp(e: _Entrega, plataforma: str) -> str:
    """Resumo curto, sempre com no máximo 1000 caracteres. Se não couber, cai a última parte opcional;
    se ainda não couber, o resumo fica mais enxuto, nível a nível."""
    for nivel in range(4):
        partes = _whatsapp_partes(e, nivel)
        while len(_juntar(partes)) > LIMITE_WHATSAPP:
            opcionais = [n for n, (_, obrigatoria) in enumerate(partes) if not obrigatoria]
            if not opcionais:
                break
            partes.pop(opcionais[-1])
        if len(_juntar(partes)) <= LIMITE_WHATSAPP:
            return _juntar(partes)
    # Último recurso: só o essencial (título, total ou erro, selo, aviso fixo e assinatura curta).
    essencial = [(f"*{SKILL_TITLE}*", True)]
    if e.nivel_operacao:
        essencial.append((_titulo_e_codigo(e.erros_operacao[0]["titulo"], e.erros_operacao[0]["codigo"], 80), True))
    elif e.tem_total:
        essencial += [(f"CBS: {_brl(e.total['vCBSSNtot'])}", True), (f"IBS: {_brl(e.total['vIBSSNtot'])}", True)]
    else:
        essencial.append((e.sem_total + ".", True))
    essencial += [(e.selo_curto, True)] if e.selo_curto else []
    essencial += [(AVISO_FIXO, True), ("CD2 Tech", True)]
    return _juntar(essencial)


# ------------------------------------------------------------------ API e linha de comando

def render(entrega: dict, formato: str, plataforma: str = "Claude") -> str:
    """Monta a entrega no formato pedido. `entrega` segue o formato do cabeçalho deste arquivo."""
    if formato not in FORMATOS:
        raise ValueError(f"formato desconhecido: {formato} (use {', '.join(FORMATOS)})")
    if not isinstance(entrega, dict) or not isinstance(entrega.get("calculo"), dict) \
            or "corpo" not in entrega["calculo"]:
        raise ValueError('a entrega precisa trazer "calculo" com a saída do calc.py (status, corpo, versao)')
    modelo = _Entrega(entrega, formato)
    plataforma = _txt(plataforma) or "Claude"
    return {"html": _render_html, "markdown": _render_markdown, "whatsapp": _render_whatsapp}[formato](modelo, plataforma)


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
    parser = argparse.ArgumentParser(description="Entrega da skill cbs-ibs-simples (CD2 Tech).")
    parser.add_argument("--input", help="entrega.json (padrão: entrada padrão)")
    parser.add_argument("--formato", required=True, choices=FORMATOS)
    parser.add_argument("--out", help="grava a entrega neste arquivo em vez de imprimir")
    parser.add_argument("--plataforma", default="Claude", help="nome da plataforma na mensagem do WhatsApp")
    args = parser.parse_args(argv)
    for fluxo in (sys.stdin, sys.stdout):
        if hasattr(fluxo, "reconfigure"):
            fluxo.reconfigure(encoding="utf-8")
    if args.input is None and sys.stdin.isatty():
        return _falhar(["Informe a entrega com --input entrega.json, ou mande o JSON pela entrada padrão."])
    try:
        dados = _ler_json(args.input)
        texto = render(dados, args.formato, args.plataforma)
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
