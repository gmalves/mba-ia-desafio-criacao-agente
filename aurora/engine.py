"""Motor de sessoes: liga o ADK Runner (com sessao persistida em SQLite) as
rotas da API.

Peças da ponte (Garantias 1 e 3):

- `enviar_mensagem`: roda o turno e coleta, dos eventos novos, as confirmações
  pendentes emitidas pelo ADK (function call sintetico `adk_request_confirmation`).
- `responder_confirmacao`: reivindica a confirmação pendente (409 se não estiver
  pendente nesta sessão), monta o Content com `FunctionResponse` no formato que
  o ADK espera e RODA DE NOVO — a retomada roteia para o agente que pediu a
  confirmação e re-executa a tool com `tool_confirmation.confirmed`.
- Tudo e aguardado sob um lock global: as duas aprovações concorrentes do passo
  14 rodam serializadas de forma segura, e a exclusividade da reserva continua
  garantida no instante da gravação pelo indice unico do Store.
"""

from __future__ import annotations

import asyncio
import warnings

from google.adk.runners import Runner
from google.adk.sessions import DatabaseSessionService
from google.genai import types

from . import config
from .agents import construir_app
from .modelo import transitorio as _erro_transitorio
from .store import Store

warnings.filterwarnings("ignore")

NOME_FUNCTION_CONFIRMACAO = "adk_request_confirmation"


class SessaoInexistente(Exception):
    """A sessao informada nao existe."""


class ConfirmacaoNaoPendente(Exception):
    """Nao existe confirmacao PENDENTE com esse id nesta sessao."""


class Engine:
    def __init__(self) -> None:
        app = construir_app()
        self.store = Store(config.DB_DADOS)
        self.sessions = DatabaseSessionService(
            db_url=f"sqlite+aiosqlite:///{config.DB_SESSOES}"
        )
        self.runner = Runner(app=app, session_service=self.sessions)
        self._lock = asyncio.Lock()

    async def iniciar(self) -> None:
        await self.store.abrir()
        from . import tools as T

        T.definir_store(self.store)

    # ------------------------------------------------------------------

    async def criar_sessao(self, apartamento: str) -> str:
        sessao = await self.sessions.create_session(
            app_name=config.APP_NAME,
            user_id=f"apt:{apartamento}",
            state={"apartamento": apartamento},
        )
        await self.store.registrar_sessao(sessao.id, apartamento)
        return sessao.id

    async def _obter(self, session_id: str):
        apartamento = await self.store.apartamento_da_sessao(session_id)
        if apartamento is None:
            raise SessaoInexistente(session_id)
        try:
            return await self.sessions.get_session(
                app_name=config.APP_NAME,
                user_id=f"apt:{apartamento}",
                session_id=session_id,
            )
        except Exception as exc:  # sessao removida do banco de sessoes
            raise SessaoInexistente(session_id) from exc

    async def _rodar(self, session_id: str, apartamento: str, conteudo: types.Content) -> dict:
        """Roda um turno, persiste confirmacoes novas e devolve a resposta da API.

        Resiliência: erros transitorios do provedor (503/UNAVAILABLE, falhas de
        transporte) no INICIO do turno (nenhuma tool executada ainda) sao
        re-tentados com backoff; se uma tool chegou a executar, o erro sobe para
        nao duplicar efeitos.
        """
        tentativa = 0
        while True:
            tentativa += 1
            textos: list[str] = []
            confirmacoes_novas: list[dict] = []
            tool_executou = False
            try:
                async for evento in self.runner.run_async(
                    user_id=f"apt:{apartamento}", session_id=session_id, new_message=conteudo
                ):
                    if evento.content and evento.content.parts:
                        for parte in evento.content.parts:
                            if getattr(parte, "text", None) and evento.author != "user":
                                textos.append(parte.text)
                    for chamada in evento.get_function_calls():
                        if chamada.name == NOME_FUNCTION_CONFIRMACAO:
                            argumentos = chamada.args or {}
                            confirmacao = argumentos.get("toolConfirmation") or {}
                            original = argumentos.get("originalFunctionCall") or {}
                            confirmacoes_novas.append(
                                {
                                    "id": chamada.id,
                                    "acao": original.get("name"),
                                    "detalhes": confirmacao.get("payload") or original.get("args") or {},
                                }
                            )
                        else:
                            tool_executou = True
            except Exception as exc:
                if tentativa < 6 and not tool_executou and _erro_transitorio(exc):
                    await asyncio.sleep(10 * tentativa)
                    continue
                raise
            break
        for c in confirmacoes_novas:
            await self.store.inserir_confirmacao(session_id, c["id"], c["acao"], c["detalhes"])
        pendentes = await self.store.confirmacoes_pendentes(session_id)
        resposta = "".join(textos).strip()
        return {"resposta": resposta, "confirmacoes_pendentes": pendentes}

    async def enviar_mensagem(self, session_id: str, texto: str) -> dict:
        sessao = await self._obter(session_id)  # valida existencia
        apartamento = str(sessao.state.get("apartamento"))
        conteudo = types.Content(role="user", parts=[types.Part(text=texto)])
        async with self._lock:
            return await self._rodar(session_id, apartamento, conteudo)

    async def responder_confirmacao(self, session_id: str, fc_id: str, confirmado: bool) -> dict:
        sessao = await self._obter(session_id)
        apartamento = str(sessao.state.get("apartamento"))
        if not await self.store.reivindicar_confirmacao(session_id, fc_id):
            raise ConfirmacaoNaoPendente(fc_id)
        conteudo = types.Content(
            role="user",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        id=fc_id,
                        name=NOME_FUNCTION_CONFIRMACAO,
                        response={"confirmed": bool(confirmado)},
                    )
                )
            ],
        )
        try:
            async with self._lock:
                resultado = await self._rodar(session_id, apartamento, conteudo)
            await self.store.definir_status_confirmacao(session_id, fc_id, "respondida")
            return resultado
        except Exception:
            # Devolve para pendente para nao travar a confirmacao em caso de erro.
            await self.store.definir_status_confirmacao(session_id, fc_id, "pendente")
            raise

    async def eventos(self, session_id: str) -> list[dict]:
        sessao = await self._obter(session_id)
        return [
            evento.model_dump(mode="json", by_alias=True, exclude_none=True)
            for evento in sessao.events
        ]
