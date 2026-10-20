"""Armazenamento SQLite (aiosqlite) dos dados do condominio.

Garantias implementadas aqui (nao dependem do que o modelo decide):

- Garantia 5 (arquivo + trecho): a exclusividade da reserva vale no INSTANTE da
  gravacao via indice unico parcial `reservas_unicas_por_area_data` sobre
  (area, data) WHERE status='ativa'. Duas gravacoes concorrentes: uma vence,
  a outra recebe IntegrityError e devolve recusa normal (sem erro de servidor).
- Codigos de reserva sao gerados pelo sistema e nunca repetem (tabela nunca
  apaga linhas; codigo aleatorio + retry em colisao).
- Cancelamento so afeta o PROPRIO apartamento (WHERE apartamento = ?).
"""

from __future__ import annotations

import json
import secrets
from pathlib import Path

import aiosqlite

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS reservas (
  codigo       TEXT PRIMARY KEY,
  apartamento  TEXT NOT NULL,
  area         TEXT NOT NULL,
  data         TEXT NOT NULL,
  status       TEXT NOT NULL DEFAULT 'ativa'
);
CREATE UNIQUE INDEX IF NOT EXISTS reservas_unicas_por_area_data
  ON reservas (area, data) WHERE status = 'ativa';
CREATE TABLE IF NOT EXISTS visitantes (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  apartamento  TEXT NOT NULL,
  nome         TEXT NOT NULL,
  data         TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS confirmacoes (
  session_id   TEXT NOT NULL,
  fc_id        TEXT NOT NULL,
  acao         TEXT,
  detalhes     TEXT,
  status       TEXT NOT NULL,           -- pendente | processando | respondida
  PRIMARY KEY (session_id, fc_id)
);
CREATE TABLE IF NOT EXISTS sessoes (
  session_id   TEXT PRIMARY KEY,
  apartamento  TEXT NOT NULL
);
"""


class Store:
    def __init__(self, db_path: Path) -> None:
        self._db_path = str(db_path)
        self._conn: aiosqlite.Connection | None = None

    async def abrir(self) -> None:
        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA busy_timeout=10000")
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()
        # Semeia os dados iniciais das reservas/visitantes se o banco estiver vazio.
        linha = await self._fetchone("SELECT COUNT(*) AS n FROM reservas")
        if linha and linha["n"] == 0:
            await self._semear_base()
        linha = await self._fetchone("SELECT COUNT(*) AS n FROM visitantes")
        if linha and linha["n"] == 0:
            await self._semear_base()

    async def fechar(self) -> None:
        if self._conn is not None:
            await self._conn.close()

    async def _fetchone(self, sql: str, params: tuple = ()) -> aiosqlite.Row | None:
        assert self._conn is not None
        cur = await self._conn.execute(sql, params)
        try:
            return await cur.fetchone()
        finally:
            await cur.close()

    async def _fetchall(self, sql: str, params: tuple = ()) -> list[aiosqlite.Row]:
        assert self._conn is not None
        cur = await self._conn.execute(sql, params)
        try:
            return list(await cur.fetchall())
        finally:
            await cur.close()

    async def _exec(self, sql: str, params: tuple = ()) -> int:
        """Executa um statement e commita; devolve rowcount."""
        assert self._conn is not None
        cur = await self._conn.execute(sql, params)
        try:
            n = cur.rowcount
        finally:
            await cur.close()
        await self._conn.commit()
        return n

    async def _semear_base(self) -> None:
        assert self._conn is not None
        for r in config.RESERVAS_BASE:
            await self._conn.execute(
                "INSERT OR IGNORE INTO reservas (codigo, apartamento, area, data, status) VALUES (?,?,?,?,'ativa')",
                (r["codigo"], r["apartamento"], r["area"], r["data"]),
            )
        for v in config.VISITANTES_BASE:
            await self._conn.execute(
                "INSERT INTO visitantes (apartamento, nome, data) VALUES (?,?,?)",
                (v["apartamento"], v["nome"], v["data"]),
            )
        await self._conn.commit()

    # ------------------------------------------------------------------- dados

    async def listar_reservas(self, apartamento: str) -> list[dict]:
        linhas = await self._fetchall(
            "SELECT codigo, area, data FROM reservas WHERE apartamento = ? AND status = 'ativa' ORDER BY data, codigo",
            (apartamento,),
        )
        return [dict(linha) for linha in linhas]

    async def listar_visitantes(self, apartamento: str) -> list[dict]:
        linhas = await self._fetchall(
            "SELECT nome, data FROM visitantes WHERE apartamento = ? ORDER BY id",
            (apartamento,),
        )
        return [dict(linha) for linha in linhas]

    async def existe_reserva_ativa(self, area: str, data: str) -> bool:
        linha = await self._fetchone(
            "SELECT 1 AS x FROM reservas WHERE area = ? AND data = ? AND status = 'ativa'",
            (area, data),
        )
        return linha is not None

    async def criar_reserva(self, apartamento: str, area: str, data: str) -> str | None:
        """Grava a reserva com exclusividade no instante da gravacao.

        Devolve o codigo gerado, ou None se ja existir reserva ativa para (area, data).
        """
        assert self._conn is not None
        for _ in range(5):
            codigo = "RSV-" + secrets.token_hex(3).upper()
            try:
                await self._conn.execute(
                    "INSERT INTO reservas (codigo, apartamento, area, data, status) VALUES (?,?,?,?,'ativa')",
                    (codigo, apartamento, area, data),
                )
                await self._conn.commit()
                return codigo
            except aiosqlite.IntegrityError as exc:
                await self._conn.rollback()
                mensagem = str(exc)
                if "reservas.codigo" in mensagem:  # colisao de codigo: tenta outro
                    continue
                return None  # (area, data) com reserva ativa: recusa normal
        return None

    async def cancelar_reserva(self, apartamento: str, area: str, data: str) -> bool:
        n = await self._exec(
            "UPDATE reservas SET status = 'cancelada' "
            "WHERE apartamento = ? AND area = ? AND data = ? AND status = 'ativa'",
            (apartamento, area, data),
        )
        return n > 0

    async def adicionar_visitante(self, apartamento: str, nome: str, data: str) -> None:
        await self._exec(
            "INSERT INTO visitantes (apartamento, nome, data) VALUES (?,?,?)",
            (apartamento, nome, data),
        )

    # ------------------------------------------------------------- sessoes

    async def registrar_sessao(self, session_id: str, apartamento: str) -> None:
        await self._exec(
            "INSERT OR REPLACE INTO sessoes (session_id, apartamento) VALUES (?,?)",
            (session_id, apartamento),
        )

    async def apartamento_da_sessao(self, session_id: str) -> str | None:
        linha = await self._fetchone(
            "SELECT apartamento FROM sessoes WHERE session_id = ?", (session_id,)
        )
        return linha["apartamento"] if linha else None

    # -------------------------------------------------------- confirmacoes

    async def inserir_confirmacao(self, session_id: str, fc_id: str, acao: str | None, detalhes: dict | None) -> None:
        await self._exec(
            "INSERT OR IGNORE INTO confirmacoes (session_id, fc_id, acao, detalhes, status) VALUES (?,?,?,?,'pendente')",
            (session_id, fc_id, acao, json.dumps(detalhes or {}, ensure_ascii=False)),
        )

    async def confirmacoes_pendentes(self, session_id: str) -> list[dict]:
        linhas = await self._fetchall(
            "SELECT fc_id, acao, detalhes FROM confirmacoes WHERE session_id = ? AND status = 'pendente' ORDER BY rowid",
            (session_id,),
        )
        return [
            {
                "id": linha["fc_id"],
                "acao": linha["acao"],
                "detalhes": json.loads(linha["detalhes"] or "{}"),
            }
            for linha in linhas
        ]

    async def reivindicar_confirmacao(self, session_id: str, fc_id: str) -> bool:
        """Marca a confirmacao como em processamento; False se nao estava pendente."""
        n = await self._exec(
            "UPDATE confirmacoes SET status = 'processando' "
            "WHERE session_id = ? AND fc_id = ? AND status = 'pendente'",
            (session_id, fc_id),
        )
        return n > 0

    async def definir_status_confirmacao(self, session_id: str, fc_id: str, status: str) -> None:
        await self._exec(
            "UPDATE confirmacoes SET status = ? WHERE session_id = ? AND fc_id = ?",
            (status, session_id, fc_id),
        )

    # ------------------------------------------------------------- restore

    async def restaurar_dados_base(self) -> None:
        """Volta reservas e visitantes ao estado de dados/*.json e limpa o
        livro de confirmacoes. As sessoes (conversas) NAO sao apagadas."""
        assert self._conn is not None
        await self._conn.execute("DELETE FROM reservas")
        await self._conn.execute("DELETE FROM visitantes")
        await self._conn.execute("DELETE FROM confirmacoes")
        for r in config.RESERVAS_BASE:
            await self._conn.execute(
                "INSERT INTO reservas (codigo, apartamento, area, data, status) VALUES (?,?,?,?,'ativa')",
                (r["codigo"], r["apartamento"], r["area"], r["data"]),
            )
        for v in config.VISITANTES_BASE:
            await self._conn.execute(
                "INSERT INTO visitantes (apartamento, nome, data) VALUES (?,?,?)",
                (v["apartamento"], v["nome"], v["data"]),
            )
        await self._conn.commit()
