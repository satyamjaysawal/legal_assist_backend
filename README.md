# Legal Assist — Backend

FastAPI chat API powering a **multi-agent legal AI assistant**: LangGraph orchestration, 7-layer memory, two-tier caching, RAG over uploaded documents, text-to-SQL on Neon Postgres, and a WebSocket lawyer chat — streamed over SSE with full pipeline visibility.

| Layer | Stack |
| --- | --- |
| **Backend** | FastAPI + LangChain + LangGraph + Groq |
| **Frontend** | Vite + React + Tailwind ([separate repo](https://github.com/satyamjaysawal/legal_assist_frontend)) |
| **Data** | MongoDB · Redis · Qdrant · Neon Postgres |

## Live production (Vercel)

| Service | URL |
| --- | --- |
| **Frontend app** | https://legal-assist-compact.vercel.app |
| **Backend API** | https://legal-assist-api.vercel.app |
| **Health** | https://legal-assist-api.vercel.app/health |
| **Swagger docs** | https://legal-assist-api.vercel.app/docs |

**GitHub:** https://github.com/satyamjaysawal/legal_assist_backend
**Vercel project:** `legal-assist-api` under [satyam-jaysawals-projects](https://vercel.com/satyam-jaysawals-projects)

## Models

| Purpose | Model |
| --- | --- |
| Main chat / reasoning (all agents) | `openai/gpt-oss-120b` (`GROQ_MODEL`) |
| Rate-limit fallback chain (429) | `openai/gpt-oss-20b` → `qwen/qwen3.6-27b` → `groq/compound-mini` |
| Embeddings (semantic memory + RAG) | `nomic-embed-text-v1.5` (Groq) · local FastEmbed fallback |
| Vision (image/document OCR) | `qwen/qwen3.6-27b` (`GROQ_VISION_MODEL`) |

## Features

- **Multi-agent orchestration** — root orchestrator classifies intent/domain/complexity and routes to 7 specialist agents (LangGraph)
- **SSE streaming v2** — every pipeline stage is visible live: memory reads, cache hits, RAG, routing, agent work, cache/memory writes
- **7-layer memory** — in-memory, short-term (Redis), user thread, long-term facts, semantic (Qdrant), episodic, procedural + user profile extraction
- **Two-tier prompt caching** — exact-match (SHA-256, Redis + RAM, 6h TTL) and semantic (cosine-similarity, disabled by default via `SEMANTIC_CACHE_ENABLED`); personal queries (`who am I?`) always bypass cache so memory answers
- **RAG** — PDF/DOCX/text/image upload → MongoDB GridFS → chunks → embeddings → Qdrant retrieval injected into answers
- **Text-to-SQL (`db_chat`)** — natural language → validated read-only SELECT on Neon Postgres (lawyer directory), executed SQL surfaced in the UI
- **Lawyer Connect** — REST rooms + WebSocket real-time chat with simulated lawyer replies in demo mode
- **Draft/Document/Email agents** — legal drafting, structured documents (RTI, notices, agreements), professional emails, PDF/DOCX/TXT export
- **HITL wizard** — guided field-filling before downloading or emailing drafts
- **Auth & journeys** — email/password (MongoDB), JWT sessions, per-chat journeys with auto titles
- **Guest mode** — 3-message limit without sign-up
- **Context compression** — old messages summarized to stay inside model context
- **Resilience** — retry-safe LLM invokes with automatic model fallback on rate limits

## Agents

| Agent | Handles intents | Role |
| --- | --- | --- |
| `orchestrator` | `*` | Root agent — analyses intent and routes to specialists |
| `assistant` | question, procedure, other | General legal Q&A |
| `researcher` | review, compare | Deep legal research, case law, document review |
| `draft` | draft | Notices, agreements, letters, petitions |
| `document_creator` | document | Structured documents — RTI, complaints, agreements |
| `email` | email | Professional legal email composition |
| `lawyer_finder` | find_lawyer | Lawyer directory search + live chat entry point |
| `db_chat` | db_query | Text-to-SQL over the Neon lawyer directory |

## Connectors

`bare_acts` · `court_api` · `indian_kanoon` · `legal_dictionary` · `legal_templates` · `neon_postgres` — listed live at `GET /connectors`.

## File structure

```
legal_assist_backend/
├── main.py               # FastAPI routes + SSE v2 pipeline
├── multi_graph.py        # LangGraph multi-agent graph
├── graph.py              # Legacy single-agent graph
├── agents/               # orchestrator + 7 specialist agents
├── connectors/           # legal data sources + Neon Postgres
├── websocket/            # lawyer_connect.py — WS chat rooms
├── memory.py             # 7-layer memory + profile extraction
├── cache.py              # exact + semantic prompt caching
├── embeddings.py         # Groq embeddings + FastEmbed fallback
├── vectordb.py           # Qdrant semantic store
├── docs.py / files.py    # document parsing + GridFS storage
├── auth.py / journeys.py # JWT auth + chat journeys
├── seed_neon.py          # seeds the Neon lawyer directory
├── requirements.txt
├── runtime.txt           # Python version for Vercel
└── vercel.json           # @vercel/python → main.py
```

Full platform documentation: [PROJECT_DOCUMENTATION.md](./PROJECT_DOCUMENTATION.md)

## Environment variables

Copy `.env.example` → `.env` for local development. **Never commit real secrets.**

| Variable | Purpose |
| --- | --- |
| `GROQ_API_KEY` | Groq API key ([console.groq.com](https://console.groq.com)) |
| `GROQ_MODEL` | Chat model (default `openai/gpt-oss-120b`) |
| `GROQ_EMBED_MODEL` | Embedding model (`nomic-embed-text-v1.5`) |
| `GROQ_VISION_MODEL` | Vision model for image OCR |
| `MONGO_URL` / `MONGO_DB` / `MONGO_FILES_BUCKET` | MongoDB + GridFS |
| `REDIS_URL` | Redis (short-term memory + exact cache) |
| `QDRANT_URL` / `QDRANT_API_KEY` | Qdrant Cloud (semantic + RAG vectors) |
| `NEON_POSTGRE_DB` | Neon Postgres pooler URL (lawyer directory / db_chat) |
| `JWT_SECRET` | Token signing secret |
| `SEMANTIC_CACHE_ENABLED` | Semantic cache kill-switch (`false` = off, default) |
| `SMTP_*` | Outbound email for the email agent |
| `CORS_ORIGINS` | Extra allowed origins (comma-separated) |

## Local setup

**Prerequisites:** Python 3.12+, a Groq API key, and the data services above.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
# fill in the keys in .env
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

Seed the lawyer directory once:

```powershell
python seed_neon.py            # or: python seed_neon.py --force
```

| Service | URL |
| --- | --- |
| API | http://127.0.0.1:8000 |
| Health | http://127.0.0.1:8000/health |
| Swagger | http://127.0.0.1:8000/docs |

## API overview

| Method | Path | Description |
| --- | --- | --- |
| GET | `/` · `/health` | Ping · health + agents + connectors |
| POST | `/auth/register` · `/auth/login` | Account creation · JWT login |
| GET/PATCH | `/auth/me` | Profile read / update |
| GET/POST/PATCH/DELETE | `/journeys…` | Chat journeys (threads) |
| POST | `/chat` · `/chat/stream` · `/chat/stream/v2` · `/chat/guest` | JSON reply · legacy SSE · **full-pipeline SSE** · guest mode |
| GET | `/memory` · `/memory/profile` · `/memory/episodes` · `/memory/preferences` | Memory inspection |
| POST/GET/DELETE | `/documents…` | Upload (PDF/DOCX/text/image) · list · download · delete |
| GET | `/connectors` · `/connectors/{name}` | Data-source status |
| GET | `/lawyers` | Lawyer directory from Neon Postgres |
| POST/GET/DELETE | `/lawyer/rooms…` | Live-chat rooms |
| WS | `/ws/lawyer/user/{room_id}` · `/ws/lawyer/lawyer/{room_id}` | Real-time chat (user / lawyer side) |
| POST | `/export/download` · `/email/send` | PDF/DOCX/TXT export · email |
| GET | `/admin/system` · `/admin/agents` · `/admin/connectors` | Admin introspection |

### `POST /chat/stream/v2` (main endpoint)

```json
{
  "messages": [{ "role": "user", "content": "Show lawyers in Mumbai with 10+ years experience" }],
  "journey_id": "<uuid>",
  "session_id": "<uuid>"
}
```

SSE event types: `thinking` · `flow` · `memory` · `retrieval` · `cache` · `agent_route` · `analysis` · `sql` · `token` · `followups` · `memory_write` · `cache_write` · `done` · `error`.

## Deploy on Vercel

```powershell
vercel link --yes
vercel env add GROQ_API_KEY production --sensitive   # + the rest of the vars
vercel --prod --yes
vercel alias set <deployment-url> legal-assist-api.vercel.app
```

> Note: Vercel serverless does not keep WebSocket connections alive — the lawyer chat works fully when the backend runs on a WebSocket-capable host (e.g. locally with uvicorn).

## Troubleshooting

| Issue | Fix |
| --- | --- |
| `GROQ_API_KEY is not configured` | Set `.env` or Vercel env `GROQ_API_KEY` |
| CORS blocked from frontend | Origin must be localhost Vite or `*.vercel.app`, or add it to `CORS_ORIGINS` |
| Empty reply / 429 | Groq free-tier daily token quota — fallback chain kicks in automatically |
| `Neon Postgres not configured` | Set `NEON_POSTGRE_DB` (read lazily, safe with load_dotenv ordering) |
| Cached error replaying | Error replies are never cached; purge `legal_assist:pcache:<sha>` in Redis if needed |
