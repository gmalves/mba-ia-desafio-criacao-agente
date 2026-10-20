"""API do assistente do Residencial Aurora (contrato do enunciado).

Rotas:
- POST /sessoes                            {apartamento} -> 201 {session_id}
- POST /sessoes/{id}/mensagens             {texto} -> 200 {resposta, confirmacoes_pendentes}
- POST /sessoes/{id}/confirmacoes          {id, confirmado} -> 200 | 409 | 404
- GET  /sessoes/{id}/eventos               -> 200 [eventos completos] | 404
- GET  /apartamentos/{apt}/reservas        -> 200 [{codigo, area, data}]
- GET  /apartamentos/{apt}/visitantes      -> 200 [{nome, data}]

As rotas de verificacao leem os dados DIRETO do banco, sem passar pelo modelo.
"""

from __future__ import annotations

import warnings
from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv(".env")

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from . import config  # noqa: F401
from .engine import ConfirmacaoNaoPendente, Engine, SessaoInexistente

warnings.filterwarnings("ignore")

ENGINE: Engine | None = None


def _engine() -> Engine:
    assert ENGINE is not None, "Engine nao inicializada"
    return ENGINE


@asynccontextmanager
async def lifespan(_: FastAPI):
    global ENGINE
    ENGINE = Engine()
    await ENGINE.iniciar()
    yield


app = FastAPI(title="Residencial Aurora", version="1.0.0", lifespan=lifespan)


class NovaSessao(BaseModel):
    apartamento: str


class Mensagem(BaseModel):
    texto: str


class RespostaConfirmacao(BaseModel):
    id: str
    confirmado: bool


@app.post("/sessoes", status_code=201)
async def criar_sessao(corpo: NovaSessao) -> dict:
    session_id = await _engine().criar_sessao(corpo.apartamento)
    return {"session_id": session_id}


@app.post("/sessoes/{session_id}/mensagens")
async def enviar_mensagem(session_id: str, corpo: Mensagem) -> dict:
    try:
        return await _engine().enviar_mensagem(session_id, corpo.texto)
    except SessaoInexistente:
        raise HTTPException(status_code=404, detail="sessao nao encontrada") from None


@app.post("/sessoes/{session_id}/confirmacoes")
async def responder_confirmacao(session_id: str, corpo: RespostaConfirmacao) -> dict:
    try:
        return await _engine().responder_confirmacao(session_id, corpo.id, corpo.confirmado)
    except SessaoInexistente:
        raise HTTPException(status_code=404, detail="sessao nao encontrada") from None
    except ConfirmacaoNaoPendente:
        raise HTTPException(
            status_code=409,
            detail="nao existe confirmacao pendente com esse id nesta sessao",
        ) from None


@app.get("/sessoes/{session_id}/eventos")
async def listar_eventos(session_id: str) -> list[dict]:
    try:
        return await _engine().eventos(session_id)
    except SessaoInexistente:
        raise HTTPException(status_code=404, detail="sessao nao encontrada") from None


@app.get("/apartamentos/{apartamento}/reservas")
async def reservas_do_apartamento(apartamento: str) -> list[dict]:
    return await _engine().store.listar_reservas(apartamento)


@app.get("/apartamentos/{apartamento}/visitantes")
async def visitantes_do_apartamento(apartamento: str) -> list[dict]:
    return await _engine().store.listar_visitantes(apartamento)
