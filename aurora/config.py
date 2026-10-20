"""Configuracao e carga dos dados do condominio."""

from __future__ import annotations

import json
import os
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DADOS = RAIZ / "dados"

APP_NAME = "aurora"
MODELO = os.environ.get("AURORA_MODELO", "gemini-flash-lite-latest")
MODELOS_FALLBACK = [
    m.strip()
    for m in os.environ.get(
        "AURORA_MODELOS_FALLBACK",
        "gemini-3.1-flash-lite,gemini-3-flash-preview,gemini-flash-latest",
    ).split(",")
    if m.strip()
]

DB_DADOS = RAIZ / "aurora_dados.db"
DB_SESSOES = RAIZ / "aurora_sessoes.db"


def _carregar(nome: str):
    return json.loads((DADOS / nome).read_text(encoding="utf-8"))


APARTAMENTOS: dict[str, str] = {
    item["numero"]: item["morador"] for item in _carregar("apartamentos.json")
}
AREAS: dict[str, dict] = {item["id"]: item for item in _carregar("areas.json")}
RESERVAS_BASE: list[dict] = _carregar("reservas.json")
VISITANTES_BASE: list[dict] = _carregar("visitantes.json")
