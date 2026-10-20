# Regra é regra: o assistente virtual do Residencial Aurora

Assistente do condomínio construído com **Google ADK** (série 2) e exposto por uma
**API FastAPI** em `http://localhost:8000`, seguindo o contrato do enunciado.
O morador conversa com um agente principal que distribui o trabalho entre
especialistas; as regras críticas do condomínio (confirmação de cobrança/acesso,
apartamento da sessão, exclusividade de reserva) vivem no **código**, não no prompt.
O enunciado original do desafio está em
https://github.com/devfullcycle/mba-ia-desafio-criacao-agente.

## Arquitetura

**Um agente principal e três especialistas** (definidos em `aurora/agents.py`).
O principal não tem tools de dados: ele só entende o pedido e **transfere**
(`transfer_to_agent`, recurso nativo do ADK para sub-agentes) para o especialista
certo. Quem decide o caminho é o modelo; cada especialista carrega apenas as tools
da sua responsabilidade:

| Agente | Responsabilidade | Como é acionado | Tools |
|---|---|---|---|
| `recepcao` (principal) | Recebe o morador, entende o pedido e encaminha | — | nenhuma tool de dados |
| `agenda` | Reservas, cancelamentos e disponibilidade de áreas | transferência do principal | `listar_minhas_reservas`, `consultar_disponibilidade`, `reservar_area`, `cancelar_reserva` |
| `portaria` | Autorização de visitantes | transferência do principal | `autorizar_visitante`, `listar_meus_visitantes` |
| `regulamento` | Dúvidas sobre o regulamento interno | transferência do principal | `consultar_regulamento` |

Por quê: o principal enxerga o mínimo (não tem acesso a dados nem ao
regulamento); cada especialista tem só as tools do seu domínio, o que reduz o
alcance de uma mensagem maliciosa e o tamanho do contexto enviado ao modelo.

**Runtime** (`aurora/engine.py`): `Runner` + `App` do ADK com
`ResumabilityConfig(is_resumable=True)` — necessário para a retomada de
confirmação voltar ao agente que pediu (o principal não intercepta a resposta).
As sessões são persistidas com `DatabaseSessionService` em SQLite
(`aurora_sessoes.db`) e os dados do condomínio em um segundo arquivo
(`aurora_dados.db`, aiosqlite com WAL e `busy_timeout`). A API (`aurora/api.py`)
expõe o contrato: `POST /sessoes`, `POST /sessoes/{id}/mensagens`,
`POST /sessoes/{id}/confirmacoes`, `GET /sessoes/{id}/eventos` e as rotas de
verificação `GET /apartamentos/{apt}/reservas` e `/visitantes` (leitura direta
do banco, sem passar pelo modelo).

### Como a confirmação funciona (o ponto que o enunciado destaca)

Quando uma tool chama `tool_context.request_confirmation(...)`, o ADK emite um
function call sintético `adk_request_confirmation` com um payload e um id. A API
(`aurora/engine.py`, `_rodar`) coleta esses ids e os devolve em
`confirmacoes_pendentes`. A resposta do morador (`POST .../confirmacoes`) monta um
`Content` com `FunctionResponse(id=<id>, name="adk_request_confirmation",
response={"confirmed": true|false})` e roda o Runner de novo: o ADK roteia a
retomada para o **mesmo agente** que pediu a confirmação e re-executa a tool com
`tool_context.tool_confirmation.confirmed` preenchido. Essa rota foi validada
com sessão persistida **e depois de reiniciar a API**.

## Garantias

### Garantia 1 — cobrança ou acesso só com confirmação

- **Arquivo/trecho**: `aurora/tools.py` — em `reservar_area`, área com taxa
  maior que zero só grava após `tool_context.tool_confirmation.confirmed`;
  sem confirmação presente, a tool chama
  `tool_context.request_confirmation(hint=..., payload={"area": ..., "data": ..., "taxa": ...})`.
  Em `autorizar_visitante` o mesmo mecanismo vale **sempre** (visita libera
  acesso). `aurora/engine.py` (`responder_confirmacao`) reivindica a confirmação
  com `store.reivindicar_confirmacao` — um `UPDATE ... WHERE status='pendente'`
  atômico: id desconhecido ou já respondido devolve `409` e nada executa
  (`aurora/store.py`, tabela `confirmacoes`).
- **Por que não depende do modelo**: a pendência nasce na tool; sem
  `confirmed=true` do fluxo de confirmações, o `INSERT` não acontece. O morador
  dizer "já confirmo" no chat não toca o banco — a rota é o único caminho.

### Garantia 2 — cada sessão pertence a um apartamento

- **Arquivo/trecho**: `aurora/tools.py` — `_apartamento()` lê
  `tool_context.state["apartamento"]`; o valor é gravado uma única vez em
  `aurora/engine.py` (`criar_sessao`, `state={"apartamento": apartamento}`).
  Nenhuma tool tem parâmetro de apartamento. `consultar_disponibilidade`
  devolve somente `{"area", "data", "livre"}` — nunca de quem é a reserva.
- **Por que não depende do modelo**: o apartamento vem do state persistido da
  sessão; não existe caminho (prompt injection ou não) que o modelo possa usar
  para apontar as tools para outra unidade.

### Garantia 3 — nada se perde no reinício

- **Arquivo/trecho**: `aurora/engine.py` —
  `DatabaseSessionService(db_url="sqlite+aiosqlite:///aurora_sessoes.db")`
  (eventos da conversa em disco) e `Store(config.DB_DADOS)` para reservas e
  visitantes.
- **Por que não depende do modelo**: são bancos em disco; reiniciar o processo
  não perde nada. Decisão registrada: `python -m aurora.restaurar` volta
  reservas/visitantes ao estado de `dados/*.json` e limpa o livro de
  confirmações, mas **não apaga as sessões**.

### Garantia 4 — o regulamento é consultado, não carregado

- **Arquivo/trecho**: `aurora/regulamento.py` — `buscar(assunto)` indexa o
  regulamento **por capítulo** e devolve apenas o capítulo mais relevante
  (com peso para o título). A tool `consultar_regulamento` (`aurora/tools.py`)
  é a única via de acesso; as instruções em `aurora/agents.py` não contêm o
  texto do regulamento (nem do principal nem dos especialistas).
- **Por que não depende do modelo**: o texto completo nunca entra no contexto;
  o que aparece nos eventos é um único capítulo — o relevante ao assunto
  perguntado — então nenhuma mensagem seguinte carrega trechos de outros
  assuntos.

### Garantia 5 — dois moradores, uma reserva

- **Arquivo/trecho**: `aurora/store.py` — índice único parcial no instante da
  gravação:
  `CREATE UNIQUE INDEX reservas_unicas_por_area_data ON reservas (area, data) WHERE status = 'ativa'`
  e `criar_reserva` traduz o `IntegrityError` em recusa normal
  (`{"reservado": false, "motivo": ...}`), sem erro de servidor. O código da
  reserva é gerado pelo sistema (`RSV-XXXXXX` aleatório) e nunca se repete: as
  linhas nunca são apagadas (cancelamento vira `status='cancelada'`).
- **Por que não depende do modelo**: a exclusividade é imposta pelo banco no
  `INSERT`; a checagem prévia (que também existe, para a resposta amigável) é só
  conveniência. Dois aprovadores simultâneos: um `INSERT` vence, o outro recebe
  `IntegrityError` e devolve recusa normal.

## Como rodar

**Pré-requisitos**: Python 3.12+ e [uv](https://docs.astral.sh/uv/) instalados;
uma chave do Google AI Studio (Gemini).

**Variáveis de ambiente** (`.env`, versionado apenas o `.env.example`):

- `GOOGLE_API_KEY` — obrigatória; sua chave do Google AI Studio.
- `AURORA_MODELO` — opcional; modelo Gemini usado pelos agentes
  (padrão: `gemini-flash-lite-latest`, escolhido por disponibilidade e cota
  no plano gratuito; qualquer modelo Gemini pode ser usado).

```bash
# 1. Clone limpo
git clone https://github.com/gmalves/mba-ia-desafio-criacao-agente.git
cd mba-ia-desafio-criacao-agente

# 2. Ambiente
cp .env.example .env
# edite .env e preencha GOOGLE_API_KEY
uv sync

# 3. Subir a API (http://localhost:8000)
uv run uvicorn aurora.api:app --host 127.0.0.1 --port 8000

# 4. (quando quiser) restaurar os dados iniciais de dados/*.json
uv run python -m aurora.restaurar
```

Os bancos SQLite (`aurora_dados.db`, `aurora_sessoes.db`) são criados e semeados
automaticamente na primeira subida. O comando de restauração pode rodar com a
API ligada; ele reescreve reservas e visitantes a partir de `dados/*.json` e
limpa o livro de confirmações, mantendo as sessões.

## Estrutura do repositório

```text
.
├── aurora/
│   ├── api.py          (rotas FastAPI do contrato)
│   ├── agents.py       (agente principal + especialistas e instruções)
│   ├── engine.py       (Runner/App ADK, sessões, confirmações, eventos)
│   ├── tools.py        (tools; apartamento da sessão; confirmações)
│   ├── store.py        (SQLite: reservas, visitantes, confirmações, sessões)
│   ├── regulamento.py  (consulta dirigida por capítulo)
│   ├── area_util.py    (normalização dos nomes de área)
│   └── restaurar.py    (comando de restauração)
├── dados/              (não alterado)
├── pyproject.toml      (google-adk==2.12.0 fixado)
└── uv.lock
```
