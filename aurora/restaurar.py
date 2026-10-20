"""Comando de restauracao: volta reservas e visitantes ao estado inicial de
dados/*.json e limpa o livro de confirmacoes. As sessoes (conversas) sao
mantidas — decisao registrada no README.

Uso: uv run python -m aurora.restaurar
"""

from __future__ import annotations

import asyncio

from . import config
from .store import Store


async def _restaurar() -> None:
    store = Store(config.DB_DADOS)
    await store.abrir()
    await store.restaurar_dados_base()
    await store.fechar()


def main() -> None:
    asyncio.run(_restaurar())
    print(
        "Dados restaurados: reservas e visitantes voltaram ao estado de dados/*.json "
        "(sessoes mantidas)."
    )


if __name__ == "__main__":
    main()
