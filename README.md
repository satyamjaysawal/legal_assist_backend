# Legal Assist — Backend

FastAPI chat API with **LangChain + LangGraph**, a **query analyser**, and **SSE streaming**, on **Groq** (`llama-3.3-70b-versatile`).

| Layer | Stack |
| --- | --- |
| **Backend** | FastAPI + LangChain + LangGraph + Groq |
| **Frontend** | Vite + React ([separate repo](https://github.com/satyamjaysawal/legal_assist_frontend)) |

## Live production (Vercel)

| Service | URL |
| --- | --- |
| **Backend API** | https://legal-assist-graph.vercel.app |
| **Health** | https://legal-assist-graph.vercel.app/health |
| **Swagger docs** | https://legal-assist-graph.vercel.app/docs |
| **Frontend** | https://legal-assist-ui.vercel.app |

**GitHub:** https://github.com/satyamjaysawal/legal_assist_backend  
**Vercel project:** `legal-assist-graph` under [satyam-jaysawals-projects](https://vercel.com/satyam-jaysawals-projects)

## Features

- LangGraph flow: **analyse → generate**
- Streaming via `graph.stream(..., stream_mode=["updates", "messages"], version="v2")`
- Query analyser classifies intent, domain, complexity, jurisdiction
- `POST /chat/stream` — SSE from LangGraph updates + LLM tokens
- `POST /chat` — same graph, full JSON reply (`graph.invoke`)
- `GET /health` — model + stack flags (no secrets)
- Groq key stays server-side (`GROQ_API_KEY`)
- Memory: in-process cache, Redis short-term session, MongoDB `legal_assist_inhouse` long-term facts

## File structure

```
legal_assist_backend/
├── main.py            # FastAPI routes
├── graph.py           # LangGraph analyser + answer nodes
├── requirements.txt
├── runtime.txt        # Python 3.12 for Vercel
├── vercel.json        # @vercel/python → main.py
├── .env.example
└── .vercelignore
```

## Environment variables

Copy `.env.example` → `.env` for local development. **Never commit real secrets.**

| Variable | Example | Purpose |
| --- | --- | --- |
| `GROQ_API_KEY` | `gsk_...` | Groq API key ([console.groq.com](https://console.groq.com)) |
| `GROQ_MODEL` | `llama-3.3-70b-versatile` | Chat model |
| `CORS_ORIGINS` | `https://legal-assist-ui.vercel.app` | Extra allowed origins (comma-separated) |

## Local setup

**Prerequisites:** Python 3.12+, Groq API key.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
# set GROQ_API_KEY in .env
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

| Service | URL |
| --- | --- |
| API | http://127.0.0.1:8000 |
| Health | http://127.0.0.1:8000/health |
| Swagger | http://127.0.0.1:8000/docs |

Run the frontend separately on http://127.0.0.1:5173 (Vite proxies `/chat` and `/health` here).

## API

| Method | Path | Description |
| --- | --- | --- |
| GET | `/` | Service ping |
| GET | `/health` | Health + model + stack |
| POST | `/chat` | Full LangGraph reply (JSON) |
| POST | `/chat/stream` | Analysis event, then streamed tokens (SSE) |

### `POST /chat`

```json
{
  "messages": [
    { "role": "user", "content": "What is an NDA?" }
  ]
}
```

```json
{
  "reply": "...",
  "model": "llama-3.3-70b-versatile"
}
```

`role` must be `user` or `assistant`. A system prompt is added on the server.

## Deploy on Vercel

1. Create/link project **`legal_assist_backend`**.
2. Entry is `main.py` via `vercel.json` (`@vercel/python`).
3. Set Production env vars.
4. Deploy:

```powershell
npm i -g vercel
vercel link --yes --project legal_assist_backend
vercel env add GROQ_API_KEY production --value "YOUR_KEY" --yes --sensitive
vercel env add GROQ_MODEL production --value "llama-3.3-70b-versatile" --yes
vercel deploy --prod
```

## Troubleshooting

| Issue | Fix |
| --- | --- |
| `GROQ_API_KEY is not configured` | Set `.env` or Vercel env `GROQ_API_KEY` |
| CORS blocked from frontend | Origin must be localhost Vite or `*.vercel.app`, or add it to `CORS_ORIGINS` |
| Empty / 502 Groq error | Check key, model name, and Groq rate limits |
