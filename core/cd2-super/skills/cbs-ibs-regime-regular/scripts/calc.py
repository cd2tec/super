"""Calculadora da skill cbs-ibs-regime-regular.

Por enquanto só `executar(requisicao)`, o que a bateria de conferência chama: uma requisição HTTP da
calculadora oficial do regime regular ({"metodo", "caminho", "corpo"}) vira a resposta da conta local
({"status", "corpo"}). A linha de comando, a porta em português e as recusas chegam na Task 11 do plano.
"""
from __future__ import annotations

import sys
from pathlib import Path

_AQUI = Path(__file__).resolve().parent
if str(_AQUI) not in sys.path:
    sys.path.append(str(_AQUI))

import rr_base  # noqa: E402  (precisa do caminho acima)
import rr_motor  # noqa: E402

ROTAS = {
    ("POST", "/calculadora/regime-geral"): rr_motor.regime_geral,
    ("POST", "/calculadora/observabilidade/regime-geral"): rr_motor.observabilidade,
}

_BASE = None


def _base():
    global _BASE
    if _BASE is None:
        _BASE = rr_base.Base()
    return _BASE


def executar(requisicao: dict, base=None, base_url: str = rr_motor.BASE_URL_PADRAO, agora=None) -> dict:
    """A resposta local ({"status", "corpo"}) de uma requisição à calculadora oficial (caminho sem o /api)."""
    rota = (requisicao.get("metodo", "GET").upper(), requisicao["caminho"])
    if rota not in ROTAS:
        raise ValueError(f"rota sem port: {rota[0]} {rota[1]}")
    status, corpo, _cabecalhos = ROTAS[rota](requisicao.get("corpo"), base or _base(), base_url, agora=agora)
    return {"status": status, "corpo": corpo}
