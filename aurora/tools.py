"""Tools do assistente — o lado CODE das garantias.

Garantia 2 (arquivo + trecho): TODAS as tools de dados usam o apartamento do
STATE DA SESSAO (`tool_context.state["apartamento"]`), definido uma unica vez
na criacao da sessao. Nenhuma tool aceita um apartamento vindo do modelo; o que
o morador escrever nao muda o apartamento usado pelas consultas e gravacoes.

Garantias 1 e 5 tambem vivem aqui: cobranca/acesso pedem confirmacao via
`tool_context.request_confirmation` (o texto do morador nunca substitui a rota
de confirmacoes), e a gravacao da reserva e exclusiva no instante do INSERT
(indice unico parcial no `Store`).
"""

from __future__ import annotations

import re
import unicodedata

from google.adk.tools import ToolContext

from . import config  # noqa: F401  (config reexportado para clareza)
from .area_util import normalizar_area
from .config import AREAS
from .store import Store

#: Store compartilhado, definido no start da API (aurora/api.py).
STORE: Store

FORMATO_DATA = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def definir_store(store: Store) -> None:
    global STORE
    STORE = store


def _apartamento(tool_context: ToolContext) -> str:
    """O apartamento autenticado vem SEMPRE do state da sessao (Garantia 2)."""
    return str(tool_context.state["apartamento"])


def _validar_data(data: str) -> str | None:
    if not data or not FORMATO_DATA.match(data.strip()):
        return "Data invalida: use o formato AAAA-MM-DD."
    return None


def _erro_area() -> dict:
    return {
        "erro": "Area desconhecida. Areas disponiveis: salao-de-festas, churrasqueira, quadra."
    }


async def listar_minhas_reservas(tool_context: ToolContext) -> dict:
    """Lista as reservas ativas do SEU apartamento (o apartamento da sessao)."""
    apartamento = _apartamento(tool_context)
    return {"apartamento": apartamento, "reservas": await STORE.listar_reservas(apartamento)}


async def consultar_disponibilidade(area: str, data: str, tool_context: ToolContext) -> dict:
    """Diz se uma area esta LIVRE ou OCUPADA na data.

    Informa somente livre/ocupada; nunca revela de quem e a reserva existente.
    """
    erro = _validar_data(data)
    if erro:
        return {"erro": erro}
    area_id = normalizar_area(area)
    if area_id is None:
        return _erro_area()
    ocupada = await STORE.existe_reserva_ativa(area_id, data)
    return {"area": area_id, "data": data, "livre": not ocupada}


async def reservar_area(area: str, data: str, tool_context: ToolContext) -> dict:
    """Reserva uma area comum para o SEU apartamento (o apartamento da sessao).

    Areas com taxa geram cobranca e a reserva so e gravada depois da
    confirmacao do morador pela rota de confirmacoes do aplicativo.
    """
    erro = _validar_data(data)
    if erro:
        return {"erro": erro}
    area_id = normalizar_area(area)
    if area_id is None:
        return _erro_area()
    taxa = float(AREAS[area_id]["taxa"])

    if await STORE.existe_reserva_ativa(area_id, data):
        return {"reservado": False, "motivo": "A data ja esta ocupada para essa area."}

    if taxa > 0:
        if not tool_context.tool_confirmation:
            tool_context.request_confirmation(
                hint=(
                    f"Confirme a reserva de {AREAS[area_id]['nome']} em {data} "
                    f"(taxa de R$ {taxa:.2f})."
                ),
                payload={"area": area_id, "data": data, "taxa": taxa},
            )
            return {"status": "aguardando confirmacao do morador"}
        if not tool_context.tool_confirmation.confirmed:
            return {"reservado": False, "motivo": "Confirmacao negada pelo morador."}

    codigo = await STORE.criar_reserva(_apartamento(tool_context), area_id, data)
    if codigo is None:
        return {"reservado": False, "motivo": "A data ja esta ocupada para essa area."}
    return {
        "reservado": True,
        "codigo": codigo,
        "area": area_id,
        "data": data,
        "taxa": taxa,
    }


async def cancelar_reserva(area: str, data: str, tool_context: ToolContext) -> dict:
    """Cancela uma reserva ATIVA do SEU apartamento (o apartamento da sessao).

    O cancelamento da propria reserva nao exige confirmacao. Nunca toca em
    reservas de outros apartamentos.
    """
    erro = _validar_data(data)
    if erro:
        return {"erro": erro}
    area_id = normalizar_area(area)
    if area_id is None:
        return _erro_area()
    cancelado = await STORE.cancelar_reserva(_apartamento(tool_context), area_id, data)
    if not cancelado:
        return {
            "cancelado": False,
            "motivo": "Nao encontrei uma reserva ativa do seu apartamento para essa area e data.",
        }
    return {"cancelado": True, "area": area_id, "data": data}


async def autorizar_visitante(nome: str, data: str, tool_context: ToolContext) -> dict:
    """Autoriza a entrada de um visitante para o SEU apartamento (libera acesso).

    Toda autorizacao fica pendente ate o morador confirmar pela rota de
    confirmacoes do aplicativo — dizer no chat que confirma nao substitui a
    confirmacao do sistema.
    """
    erro = _validar_data(data)
    if erro:
        return {"erro": erro}
    if not nome or not nome.strip():
        return {"erro": "Informe o nome do visitante."}
    nome = nome.strip()

    if not tool_context.tool_confirmation:
        tool_context.request_confirmation(
            hint=f"Confirme a autorizacao de {nome} para o dia {data}.",
            payload={"nome": nome, "data": data},
        )
        return {"status": "aguardando confirmacao do morador"}
    if not tool_context.tool_confirmation.confirmed:
        return {"autorizado": False, "motivo": "Confirmacao negada pelo morador."}

    await STORE.adicionar_visitante(_apartamento(tool_context), nome, data)
    return {"autorizado": True, "nome": nome, "data": data}


async def listar_meus_visitantes(tool_context: ToolContext) -> dict:
    """Lista as autorizacoes de visita do SEU apartamento (o da sessao)."""
    apartamento = _apartamento(tool_context)
    return {"apartamento": apartamento, "visitantes": await STORE.listar_visitantes(apartamento)}


def consultar_regulamento(assunto: str) -> dict:
    """Consulta o regulamento interno sobre um assunto e devolve APENAS o
    capitulo relevante (nunca o regulamento inteiro)."""
    from .regulamento import buscar

    return {"trecho": buscar(assunto)}
