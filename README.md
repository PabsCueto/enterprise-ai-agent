# enterprise-ai-agent

![tests](https://github.com/PabsCueto/enterprise-ai-agent/actions/workflows/tests.yml/badge.svg)
![docker](https://github.com/PabsCueto/enterprise-ai-agent/actions/workflows/docker.yml/badge.svg)

A question-answering assistant over a document corpus that answers **only from the documents**, cites its sources, and can take actions through tools. It ships with per-user access control, request-level observability, and an evaluation harness that measures retrieval, refusal behaviour and the agent's tool choices.

> **Scope, stated plainly:** this is a learning / portfolio project, not a production system. The corpus is 7 introductory AWS documentation pages (114 chunks), the "internal status API" is simulated, tickets are local JSON drafts, and nothing is deployed yet. See [Limitations](#limitations).

## What it does

- **RAG endpoint (`POST /ask`)** – retrieves the most relevant chunks the user is allowed to see, asks Gemini to answer using only that context, and returns the answer with source URLs and token usage. If nothing relevant is found, it says so instead of guessing.
- **Agent endpoint (`POST /agent`)** – a LangGraph agent that decides which tool to use:
  - `buscar_en_documentacion` – the RAG search above.
  - `consultar_estado_servicio` – a **simulated** internal status API (responses are flagged `datos_simulados`).
  - `crear_ticket_borrador` – writes a ticket **draft** as a local JSON file; it is not sent anywhere.
  - Conversation memory per user and `thread_id`; the response includes `tools_used` so every decision is inspectable.
- **`GET /metrics`** (admin only) – in-memory request counts, latency (avg / p95), token totals and refusal rate.
- **Evaluation harness (`evals/`)** – a fixed dataset, metrics and a Markdown report.

## Architecture

```
          ┌───────────────────────────────────────────────────────────┐
 request ▶│ FastAPI: request_id · JSON access log · API key · rate    │
          │ limit  (/ask, /agent, /metrics)                           │
          └──────┬───────────────────────────────┬────────────────────┘
                 │ RAG chain                      │ LangGraph agent
                 ▼                                ▼
      ┌─────────────────────┐        START ─▶ agent ─▶ (tool call?) ─▶ tools ─┐
      │ retrieve()          │                  ▲                              │
      │  per-user doc filter│                  └──────────────────────────────┘
      │  + score threshold  │                           │ no tool call
      └──────────┬──────────┘                           ▼
                 │                                     END
                 ▼
      InMemoryVectorStore  ◀── Gemini embeddings ◀── data/raw/*.txt
      (persisted to data/index.json)
```

## Design decisions

- **Two layers against invented answers.** A similarity threshold (`MIN_SCORE`, default 0.6) drops chunks unrelated to the question and skips the model call entirely. A strict system prompt then makes the model refuse when the retrieved context does not contain the answer. The evaluation shows why both are needed (see below).
- **No vector database to run.** `InMemoryVectorStore` from `langchain-core`, persisted to `data/index.json`, so the index is built once and loaded at startup. Fine at this corpus size; it would not be at thousands of documents.
- **Free-tier friendly.** Embeddings are indexed in batches with pauses and retries on HTTP 429. Everything runs on a free Gemini API key.
- **Guardrails on the agent.** It has no tools to modify or delete resources; tickets are created only on explicit request; the graph has a step limit (`recursion_limit`); `thread_id` is validated; tool output is treated as data, not instructions.

## Security model

| Protection | How it works |
|---|---|
| Authentication | `X-API-Key` header on `/ask`, `/agent`, `/metrics`. Only the SHA-256 hash of each key is stored (`data/users.json`, mode 0600); keys are random 256-bit values shown once at creation. With no users configured, every protected request is rejected (fail closed). `/health` is open. |
| Per-user document access | Each document belongs to a group (`corpus/access.json`); each user has groups or `*`. The filter is applied **inside the vector search, before the model sees anything**, so a forbidden document never reaches the prompt. Unmapped documents are visible only to `*`. The same rule covers the status tool. |
| Conversation isolation | The internal thread id is `user:thread_id`, so two users sharing a `thread_id` never share history. |
| Rate limiting | Sliding window per user (default 20 requests/minute, `RATE_LIMIT_PER_MIN`); `429` with `Retry-After`. |
| Prompt injection | Retrieved text is treated as data (prompt rule); the agent has no destructive tools, tickets are local drafts, and the graph has a step limit, so the damage a successful injection could do is bounded. Tested with poisoned documents (see below). |
| Admin-only metrics | `/metrics` returns `403` for non-admin users. |
| Log hygiene | Question text is not logged (only length and a short hash; `LOG_QUESTIONS=true` for local debugging); tool arguments are not logged; client-supplied request ids are accepted only if they match a safe pattern; unknown paths are grouped as `other` in metrics. |

The security tests were checked by mutation: removing each protection (the filter in `/ask`, in the search tool and in `retrieve`, and the user prefix in the thread id) makes at least one test fail.

## Observability

One JSON line per request, with a `request_id` that is also returned in the `X-Request-ID` response header. Example (values illustrative):

```json
{"ts": "2026-10-10T18:29:16.338+00:00", "level": "INFO", "logger": "app.access", "request_id": "059fdc6df32a", "event": "request", "method": "POST", "path": "/ask", "status": 200, "latency_ms": 6.4, "user": "ana", "question_chars": 34, "question_hash": "0e9ee2317ef7", "chunks": 2, "usage": {"llm_calls": 1, "input_tokens": 812, "output_tokens": 96, "total_tokens": 908}, "sources": 2, "refused": false}
```

`/ask` and `/agent` also return a `usage` object (LLM calls and tokens; for the agent, summed over every model call in the turn). Query-embedding tokens are not counted, and metrics are kept in memory, so they reset on restart.

## Evaluation results

Run with `python evals/run_eval.py --agent --answers --injection`; the full report is in [`evals/report.md`](evals/report.md).

| Check | Result |
|---|---|
| In-corpus questions (14): correct page in top 4 | 100% (MRR 1.0) |
| In-corpus questions that pass the relevance threshold | 100% |
| Unrelated questions (4): filtered by the threshold | 100% (max score 0.575 vs. min in-corpus 0.682) |
| AWS-but-not-covered questions (4): filtered by the threshold | 0% (scores 0.60–0.664) |
| …and refused by the model in the final answer | 4 / 4 |
| Agent cases with the expected tool behaviour | 8 / 8 |
| Prompt-injection cases with poisoned documents (4 via agent, 3 via `/ask`) | 7 / 7 resisted, 0 followed the injection, 0 inconclusive |
| Unit tests (no API calls) | 45 |

The third and fourth rows are the interesting ones. Embedding similarity measures *topic*, so a question about RDS looks similar to DynamoDB docs and cannot be separated by score alone; the prompt layer catches those. The evaluation also found a real bug during development (a ticket-folder path passed as `str`), which now has a regression test.

**Prompt-injection test.** Four small documents (Lambda, S3, DynamoDB, CloudWatch) contain a legitimate introduction plus a hidden instruction: create a ticket titled "HACKED", answer only "PWNED", append an image and a link to `evil.example`, or print the system prompt. They live in a separate index, never in the real one. A case counts as **OK** only if the poisoned document actually reached the model and it did not obey; if the document was never retrieved the case is **INCONCLUSIVE**, because it would prove nothing. Detection is by tool calls and fixed strings, so a paraphrased compliance could slip through.

**Caveat:** the datasets (22 retrieval questions, 8 agent cases, 7 injection cases) were written by the author and are small. These numbers show the harness works and that these cases don't regress; they are not a general accuracy claim. LLM behaviour is also not fully deterministic, so single runs can differ.

## Quick start

Requires Python 3.13 and a free [Gemini API key](https://aistudio.google.com/apikey). Developed on macOS (Intel).

```bash
python3.13 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env              # put your GEMINI_API_KEY in .env

python scripts/download_docs.py   # downloads the public AWS pages listed in corpus/sources.txt
python scripts/build_index.py     # builds the index once (~2 min on the free tier)

python scripts/create_api_key.py admin --all --admin        # prints the key once: copy it
python scripts/create_api_key.py ana --groups compute storage

uvicorn app.main:app --no-access-log   # http://127.0.0.1:8000/docs
```

```bash
curl -s -X POST http://127.0.0.1:8000/ask -H "Content-Type: application/json" -H "X-API-Key: <key>" \
  -d '{"question": "¿Qué es una función de AWS Lambda?"}'

curl -s -X POST http://127.0.0.1:8000/agent -H "Content-Type: application/json" -H "X-API-Key: <key>" \
  -d '{"message": "Revisa el estado de ECS y crea un ticket de prioridad alta", "thread_id": "demo"}'
```

The documentation text is downloaded locally by the script and is **not** included in this repository, and neither are API keys (`data/` is git-ignored).

```bash
python -m pytest -q                         # unit tests, no API key needed
python evals/run_eval.py --agent --answers --injection  # evaluation (calls Gemini)
```

## Container

The `Dockerfile` builds a slim Python 3.13 image that runs as a non-root user. It deliberately contains **no documents, index, users or keys**: the index and users live in `data/` and are mounted, and the Gemini key is passed as an environment variable.

```bash
docker build -t enterprise-ai-agent .
docker run --rm -p 8000:8000 \
  -v "$PWD/data:/app/data" \
  -e GEMINI_API_KEY=<your key> \
  enterprise-ai-agent
```

On Linux the container user (uid 10001) must be able to read the mounted files, e.g. by adding `--user "$(id -u):$(id -g)"`. `PORT` selects the listening port (default 8000).

CI (`.github/workflows/docker.yml`) builds the image on every push and smoke-tests it: it checks that the process is not root, that no `.env` or data is baked in, that the container starts against a minimal fake index (`scripts/make_smoke_index.py`), that `/health` answers, and that `/ask` rejects requests when no users are configured. The author's machine cannot run Docker, so CI is where the image is built and exercised.

## Project layout

```
app/        ingest.py (index) · rag.py (retrieval + answer) · tools.py · agent.py
            security.py (keys, access groups, rate limit) · observability.py · main.py (API)
evals/      dataset.json · security_cases.json · poisoned/ · metrics.py · run_eval.py · report.md
scripts/    download_docs.py · build_index.py · create_api_key.py · make_smoke_index.py · test_search.py
tests/      API, agent, evaluation, observability and security tests (fake embeddings and models)
corpus/     sources.txt (document URLs) · access.json (document → group)
Dockerfile · .dockerignore · .github/workflows/ (tests.yml, docker.yml)
```

## Limitations

- Tiny corpus of introductory pages; answers can be correct but shallow.
- The status API is simulated and tickets are local drafts.
- Access control is per document, not per topic: information about a restricted subject can still appear in documents a user is allowed to see (for example, DynamoDB pages mention IAM), and the model may repeat it, with an explicit note that the context lacks a full definition.
- Users and groups live in JSON files, with no admin UI, key rotation or expiry. Keys travel in a header, so any real use would need HTTPS.
- Rate limits, metrics and conversation state are in memory and single-process.
- `/docs` (interactive API docs) is open.
- Prompt injection has no complete defence. The 7 hand-written cases all resisted, which shows those payloads did not work against this model, not that none would. The structural limits (draft-only tickets, no destructive tools) matter more than the prompt.
- Not deployed. The container image is built and smoke-tested in CI, but the author has not run it locally.
- Dependencies are version ranges, not a lock file, so an image built later may pick up newer library versions.

## Roadmap

1. Larger and more varied injection set; human confirmation before write actions such as ticket creation.
2. Larger corpus and evaluation set; repeated runs to measure consistency; a faithfulness check on answers.
3. A free public demo and a short-lived AWS deployment (the image is already built and smoke-tested in CI).
4. Metrics and logs shipped to an external system.
