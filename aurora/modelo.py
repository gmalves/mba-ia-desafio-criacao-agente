"""Modelo Gemini com failover de modelos na MESMA chamada.

O plano gratuito do Google AI Studio sofre picos de indisponibilidade (HTTP 503
UNAVAILABLE) e cotas por modelo (429). Este wrapper tenta o modelo principal e,
em falha transitoria, cai para os modelos de reserva sem derrubar o turno.
Erros nao transitorios (400, 401 etc.) sobem imediatamente.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from google.adk.models import BaseLlm, LlmRequest, LlmResponse
from google.adk.models.google_llm import Gemini

_MARCADORES = (
    "ServerError",
    "503",
    "UNAVAILABLE",
    "429",
    "RESOURCE_EXHAUSTED",
    "ConnectError",
    "ReadTimeout",
    "RemoteProtocolError",
    "ServiceUnavailable",
)


def transitorio(exc: BaseException) -> bool:
    """True para falhas transitorias do provedor/transporte (cadeia de causas)."""
    atual: BaseException | None = exc
    vistos = 0
    while atual is not None and vistos < 10:
        nome = type(atual).__name__
        texto = str(atual)
        if any(marca in nome or marca in texto for marca in _MARCADORES):
            return True
        atual = atual.__cause__ or atual.__context__
        vistos += 1
    return False


_LLMS: dict[str, Gemini] = {}


def _gemini(nome: str) -> Gemini:
    if nome not in _LLMS:
        _LLMS[nome] = Gemini(model=nome)
    return _LLMS[nome]


class LlmComFailover(BaseLlm):
    """Roteia para o proximo modelo da cadeia quando o atual falha transitoriamente."""

    fallbacks: list[str] = []

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        ultimo_erro: BaseException | None = None
        for nome in [self.model, *self.fallbacks]:
            try:
                async for resposta in _gemini(nome).generate_content_async(llm_request, stream):
                    yield resposta
                return
            except Exception as exc:  # noqa: BLE001 - decide-se pela transitoriedade
                ultimo_erro = exc
                if not transitorio(exc):
                    raise
        assert ultimo_erro is not None
        raise ultimo_erro
