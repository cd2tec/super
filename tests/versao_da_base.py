"""Âncora de versão para os testes que afirmam o conteúdo de uma base da Receita específica.

O robô de conferência troca a base da `cbs-ibs-simples` quando a Receita publica tabelas novas. Um teste
que confere um comportamento tira a amostra da própria base; um teste cujo sentido é o conteúdo de uma
versão (a evidência de uma revisão, um teste da Receita portado com os ids daquela base) leva
`@ancorado_na("V0073")` e é pulado, com o motivo, quando a base da skill está em outra versão.

Os testes de consistência (a base bate com o meta.json, o perfil da sugestão só cita classificações que
existem) não usam esta âncora: eles falham de propósito quando o dado não fecha, e barram o robô.
"""
import functools
import importlib.util
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "core" / "cd2-super" / "skills" / "cbs-ibs-simples" / "scripts"


def _simples_base():
    if "simples_base" not in sys.modules:
        spec = importlib.util.spec_from_file_location("simples_base", SCRIPTS / "simples_base.py")
        modulo = importlib.util.module_from_spec(spec)
        sys.modules["simples_base"] = modulo
        try:
            spec.loader.exec_module(modulo)
        except BaseException:
            sys.modules.pop("simples_base", None)
            raise
    return sys.modules["simples_base"]


def base_completa_da_skill(skill: str):
    """A base completa montada das migrações, só quando ela é da `skill`; senão None (o teste pula).

    O robô exporta `CD2_BASE_COMPLETA` junto de `CD2_BASE_COMPLETA_SKILL`: com mais de uma skill, a base
    completa de uma não pode ser lida pelo teste de outra. Nenhum teste lê `CD2_BASE_COMPLETA` direto.
    """
    caminho = os.environ.get("CD2_BASE_COMPLETA")
    if not caminho or os.environ.get("CD2_BASE_COMPLETA_SKILL") != skill:
        return None
    return Path(caminho)


@functools.lru_cache(maxsize=None)
def versao_da_base() -> str:
    """A versão da base que a skill leva (Base().versao_base()["versao"])."""
    with _simples_base().Base() as base:
        return base.versao_base()["versao"]


def ancorado_na(versao: str):
    """Pula o teste (ou todos os testes da classe) quando a base da skill não está em `versao`."""
    def decorar(alvo):
        if isinstance(alvo, type):
            for nome in list(vars(alvo)):
                if nome.startswith("test") and callable(vars(alvo)[nome]):
                    setattr(alvo, nome, decorar(vars(alvo)[nome]))
            return alvo

        @functools.wraps(alvo)
        def teste(self, *args, **kwargs):
            atual = versao_da_base()
            if atual != versao:
                self.skipTest(f"âncora da {versao}: o teste afirma o conteúdo dessa base, e a da skill está na "
                              f"{atual}. O comportamento segue conferido pelos testes com amostra da base e pela "
                              "conferência com a calculadora oficial.")
            return alvo(self, *args, **kwargs)
        return teste
    return decorar
