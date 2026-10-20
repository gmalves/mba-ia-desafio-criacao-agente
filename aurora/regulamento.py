"""Consulta ao regulamento por assunto: devolve APENAS o capitulo relevante.

Garantia 4 (arquivo + trecho): o agente principal NAO recebe o regulamento nas
instrucoes; a consulta passa pela tool `consultar_regulamento`, que retorna um
único capitulo (o mais relevante para o assunto). Assim, nenhum evento da
sessao carrega trechos de capitulos que tratam de outros assuntos, e o texto
completo nunca entra no historico da conversa.
"""

from __future__ import annotations

import unicodedata

from . import config

_PALAVRAS_IGNORADAS = {
    "ate", "que", "horas", "horario", "hora", "funciona", "funcionamento",
    "aos", "as", "o", "a", "os", "um", "uma", "uns", "umas", "de", "do", "da",
    "dos", "das", "no", "na", "nos", "nas", "em", "para", "pra", "por", "com",
    "sem", "sobre", "qual", "quais", "quando", "onde", "como", "e", "ou", "se",
    "meu", "minha", "meus", "minhas", "posso", "pode", "podemos", "permite",
    "permitido", "regra", "regras", "regulamento", "diz", "diga", "correto",
    "sao", "esta", "estao", "ser", "tem", "ha",
}

_SINONIMOS = {
    "barulho": ["silencio", "ruido", "ruidos"],
    "cachorro": ["animal", "animais", "cao", "caes", "estimacao"],
    "mudanca": ["mudancas"],
    "reforma": ["obras", "obra"],
    "obra": ["reformas", "reforma"],
    "carro": ["veiculo", "veiculos", "garagem"],
    "lixo": ["reciclagem", "coleta"],
    "festa": ["salao", "festas"],
    "visitante": ["visitantes", "portaria", "visita"],
}


def _normalizar(texto: str) -> str:
    sem_acentos = unicodedata.normalize("NFD", texto.lower())
    sem_acentos = "".join(c for c in sem_acentos if unicodedata.category(c) != "Mn")
    return sem_acentos


def _capitulos() -> list[dict]:
    texto = (config.DADOS / "regulamento.md").read_text(encoding="utf-8")
    capitulos: list[dict] = []
    atual: dict | None = None
    for linha in texto.splitlines():
        if linha.startswith("## "):
            atual = {"titulo": linha[3:].strip(), "linhas": [linha]}
            capitulos.append(atual)
        elif atual is not None:
            atual["linhas"].append(linha)
    return [
        {"titulo": c["titulo"], "texto": "\n".join(c["linhas"]).strip()} for c in capitulos
    ]


def _termos(assunto: str) -> list[str]:
    base = _normalizar(assunto).replace("?", " ").replace("!", " ")
    termos: list[str] = []
    for t in base.split():
        if len(t) > 2 and t not in _PALAVRAS_IGNORADAS:
            termos.append(t)
            termos.extend(_SINONIMOS.get(t, []))
    return termos


def buscar(assunto: str) -> str:
    """Devolve o capitulo mais relevante para o assunto (apenas um)."""
    capitulos = _capitulos()
    termos = _termos(assunto)
    if not termos:
        return "Nao identifiquei o assunto no regulamento. Reformule a pergunta."
    melhor = None
    melhor_pontos = 0
    for capitulo in capitulos:
        corpo = _normalizar(capitulo["texto"])
        titulo = _normalizar(capitulo["titulo"])
        pontos = sum(3 if t in titulo else (1 if t in corpo else 0) for t in termos)
        if pontos > melhor_pontos:
            melhor, melhor_pontos = capitulo, pontos
    if melhor is None:
        return "O regulamento nao trata desse assunto."
    return melhor["texto"]
