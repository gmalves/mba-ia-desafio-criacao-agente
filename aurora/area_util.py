"""Normalizacao de nomes de area falados pelo morador para os ids de dados/areas.json."""

from __future__ import annotations

import unicodedata

from .config import AREAS

# Apelidos aceitos alem dos ids e nomes oficiais.
_APELIDOS = {
    "salao": "salao-de-festas",
    "salao de festas": "salao-de-festas",
    "salao de festa": "salao-de-festas",
    "festas": "salao-de-festas",
    "salão": "salao-de-festas",
    "churrasqueira": "churrasqueira",
    "churrasco": "churrasqueira",
    "quadra": "quadra",
    "quadra poliesportiva": "quadra",
    "quadra de esportes": "quadra",
}


def _normalizar(texto: str) -> str:
    sem_acentos = unicodedata.normalize("NFD", texto.strip().lower())
    sem_acentos = "".join(c for c in sem_acentos if unicodedata.category(c) != "Mn")
    return " ".join(sem_acentos.replace("-", " ").replace("_", " ").split())


def normalizar_area(nome: str) -> str | None:
    """Devolve o id da area (chave de dados/areas.json) ou None se desconhecida."""
    chave = _normalizar(nome)
    if chave in AREAS:
        return chave
    if chave in _APELIDOS:
        return _APELIDOS[chave]
    for area_id, area in AREAS.items():
        if chave == _normalizar(area["nome"]):
            return area_id
    # Variacoes com artigos/preposicoes ("a quadra", "o salao", "salao de festas do predio")
    for termo, area_id in (
        ("salao", "salao-de-festas"),
        ("festas", "salao-de-festas"),
        ("churrasqueira", "churrasqueira"),
        ("quadra", "quadra"),
    ):
        if termo in chave:
            return area_id
    return None
