# enterprise-ai-agent

A question-answering assistant over a document corpus that answers **only from the documents**, cites its sources, and can take actions through tools. It also ships with an evaluation harness that measures retrieval, refusal behaviour and the agent's tool choices.

> **Scope, stated plainly:** this is a learning / portfolio project, not a production system. The corpus is 7 introductory AWS documentation pages (114 chunks), the "internal status API" is simulated, tickets are local JSON drafts, and nothing is deployed yet. See [Limitations](#limitations).

## What it does

- **RAG endpoint (`POST /ask`)** – retrieves the most relevant chunks, asks Gemini to answer using only that context, and returns the answer with the source URLs. If nothing relevant is found, it says so instead of guessing.
- **Agent endpoint (`POST /agent`)** – a LangGraph agent that decides which tool to use:
  - `buscar_en_documentacion` – the RAG search above.
  - `consultar_estado_servicio` – a **simulated** internal status API (responses are flagged `datos_simulados`).
  - `crear_ticket_borrador` – writes a ticket **draft** as a local JSON file; it is not sent anywhere.
  - Conversation memory per `thread_id`; the response includes `tools_used` so every decision is inspectable.
- **Evaluation harness (`evals/`)** – a fixed dataset, metrics and a Markdown report.

## Architecture

```
                    ┌────────────────────────────┐
 question ─────────▶│ FastAPI  (/ask, /agent)    │
                    └──────┬──────────────┬──────┘
                           │              │
                  RAG chain│              │ LangGraph agent
                           ▼              ▼
              ┌───────────────────┐   START ─▶ agent ─▶ (tool call?) ─▶ tools ─┐
              │ retrieve()        │             ▲                              │
              │  vector search    │             └──────────────────────────────┘
              │  + score threshold│                      │ no tool call
              └─────────┬─────────┘                      ▼
                        │                               END
                        ▼
              InMemoryVectorStore  ◀── Gemini embeddings ◀── data/raw/*.txt
              (persisted to data/index.json)
```

## Design decisions

- **Two layers against invented answers.** A similarity threshold (`MIN_SCORE`, default 0.6) drops chunks unrelated to the question and skips the model call entirely. A strict system prompt then makes the model refuse when the retrieved context does not contain the answer. The evaluation shows why both are needed (see below).
- **No vector database to run.** `InMemoryVectorStore` from `langchain-core`, persisted to `data/index.json`, so the index is built once and loaded at startup. Fine at this corpus size; it would not be at thousands of documents.
- **Free-tier friendly.** Embeddings are indexed in batches with pauses and retries on HTTP 429. Everything runs on a free Gemini API key.
- **Guardrails on the agent.** It has no tools to modify or delete resources; tickets are created only on explicit request; the graph has a step limit (`recursion_limit`); `thread_id` is validated; tool output is treated as data, not instructions.

## Evaluation results

Run with `python evals/run_eval.py --agent --answers`; the full report is in [`evals/report.md`](evals/report.md).

| Check | Result |
|---|---|
| In-corpus questions (14): correct page in top 4 | 100% (MRR 1.0) |
| In-corpus questions that pass the relevance threshold | 100% |
| Unrelated questions (4): filtered by the threshold | 100% (max score 0.575 vs. min in-corpus 0.682) |
| AWS-but-not-covered questions (4): filtered by the threshold | 0% (scores 0.60–0.664) |
| …and refused by the model in the final answer | 4 / 4 |
| Agent cases with the expected tool behaviour | 8 / 8 |
| Unit tests (no API calls) | 19 |

The third and fourth rows are the interesting ones. Embedding similarity measures *topic*, so a question about RDS looks similar to DynamoDB docs and cannot be separated by score alone; the prompt layer catches those. The evaluation also found a real bug during development (a ticket-folder path passed as `str`), which now has a regression test.

**Caveat:** the dataset (22 retrieval questions, 8 agent cases) was written by the author and is small. These numbers show the harness works and that these cases don't regress; they are not a general accuracy claim. LLM behaviour is also not fully deterministic, so single runs can differ.

## Quick start

Requires Python 3.13 and a free [Gemini API key](https://aistudio.google.com/apikey). Developed on macOS (Intel).

```bash
python3.13 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env              # put your GEMINI_API_KEY in .env

python scripts/download_docs.py   # downloads the public AWS pages listed in corpus/sources.txt
python scripts/build_index.py     # builds the index once (~2 min on the free tier)
uvicorn app.main:app --reload     # http://127.0.0.1:8000/docs
```

```bash
curl -s -X POST http://127.0.0.1:8000/ask -H "Content-Type: application/json" \
  -d '{"question": "¿Qué es una función de AWS Lambda?"}'

curl -s -X POST http://127.0.0.1:8000/agent -H "Content-Type: application/json" \
  -d '{"message": "Revisa el estado de ECS y crea un ticket de prioridad alta", "thread_id": "demo"}'
```

The documentation text is downloaded locally by the script and is **not** included in this repository.

```bash
python -m pytest -q                         # unit tests, no API key needed
python evals/run_eval.py --agent --answers  # evaluation (calls Gemini)
```

## Project layout

```
app/        ingest.py (index) · rag.py (retrieval + answer) · tools.py · agent.py · main.py (API)
evals/      dataset.json · metrics.py · run_eval.py · report.md
scripts/    download_docs.py · build_index.py · test_search.py
tests/      API, agent and evaluation tests (fake embeddings and models)
corpus/     sources.txt (URLs of the documents)
```

## Limitations

- Tiny corpus of introductory pages; answers can be correct but shallow.
- The status API is simulated and tickets are local drafts.
- No authentication or per-user document access control yet; `thread_id` is not tied to a user.
- No tracing, latency or token-cost monitoring yet.
- Prompt-injection resistance is only covered by prompt rules, not yet tested with poisoned documents.
- Not deployed. Single-process, in-memory conversation state.

## Roadmap

1. Observability: structured logs, per-request latency and token usage.
2. Security: access control per user, and tests with adversarial documents.
3. Larger corpus and evaluation set; repeated runs to measure consistency.
4. Container image built in CI and a short-lived cloud deployment for demos.
