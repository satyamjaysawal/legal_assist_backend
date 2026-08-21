# Legal AI Assistant — Complete Project Documentation

Everything this project does, how it's built, and what's implemented — features, agents, memory, caching, RAG, database, real-time chat, and deployment.

## Quick links

| Item | URL |
| --- | --- |
| **Live app (frontend)** | https://legal-assist-agentic.vercel.app |
| **Backend API** | https://legal-assist-api.vercel.app |
| **API health** | https://legal-assist-api.vercel.app/health |
| **Swagger docs** | https://legal-assist-api.vercel.app/docs |
| **Backend repo** | https://github.com/satyamjaysawal/legal_assist_backend |
| **Frontend repo** | https://github.com/satyamjaysawal/legal_assist_frontend |

---

## 1. What is this project?

A **legal AI assistant for India** — users ask legal questions in plain language and get answers, drafts, documents, research, and lawyer connections. Under the hood it's a **multi-agent system**: a root orchestrator reads the intent of every query and routes it to the right specialist agent, while a 7-layer memory system, two-tier caching, and document RAG make every reply personal, fast, and grounded.

The entire pipeline is **streamed live to the UI** — users can watch memory loads, cache hits, routing decisions, executed SQL, and memory writes as they happen.

---

## 2. Architecture overview

```
User (React UI)
   │  POST /chat/stream/v2  (SSE)
   ▼
FastAPI (main.py)
   ├─ Auth (JWT) → Journey (chat thread)
   ├─ Memory load (7 layers) → profile + facts injected
   ├─ Prompt cache check (exact → semantic) → personal-query bypass
   ├─ RAG retrieval (Qdrant) over uploaded documents
   ▼
LangGraph multi-agent graph (agents/multi_agent_graph.py)
   Orchestrator (root agent: intent · domain · complexity)
   ├─ assistant        — general legal Q&A           [tools: define_legal_term]
   ├─ researcher       — deep research / review      [tools: define_legal_term, search_bare_acts]
   ├─ draft            — legal drafting              [tools: list_legal_templates, get_legal_template]
   ├─ document_creator — structured documents        [tools: list_legal_templates, get_legal_template]
   ├─ email            — email composition           [deterministic]
   ├─ lawyer_finder    — lawyer directory + live chat[tools: list_lawyers, query_lawyer_database]
   └─ db_chat          — text-to-SQL on Neon Postgres[tools: query_lawyer_database]

   Every specialist runs an AGENTIC TOOL LOOP (LangChain bind_tools +
   LangGraph agent⇄ToolNode) — the LLM decides when to call which tool
   with which arguments (see §3.9 for the reusable pattern).
   ▼
Reply streamed token-by-token
   ├─ Follow-up generator
   ├─ Cache save (exact always · semantic if enabled · errors never)
   └─ Memory save (all 7 layers + profile extraction)
```

**Infra:** MongoDB (users, journeys, facts, GridFS files) · Redis (short-term memory + exact cache) · Qdrant Cloud (semantic memory + RAG vectors) · Neon Postgres (lawyer directory) · Groq (LLMs + embeddings).

---

## 3. Implemented agents

All agents are registered in a central registry and discovered by the orchestrator. Source: `agents/`.
Every specialist except `email` is **agentic** — its LLM has LangChain
tools bound via `bind_tools` and calls them on demand inside a LangGraph
tool loop (`agents/tool_loop_runner.py` → `run_agent_with_tools`).

### 3.1 Orchestrator (`orchestrator_agent.py`) — Root agent
- Classifies every query: **intent**, **domain**, **complexity**, **jurisdiction**, on-topic check.
- Produces a refined query and picks the specialist (`route_to`).
- Intents: `question`, `procedure`, `review`, `compare`, `draft`, `document`, `email`, `find_lawyer`, `db_query`, `other`.

### 3.2 Assistant (`assistant_agent.py`)
- Handles `question`, `procedure`, `other`.
- General legal Q&A, step-by-step procedures, rights and remedies — personalized with user profile and memory.
- **Tools bound:** `define_legal_term` (looks up a term's exact meaning before explaining it).

### 3.3 Researcher (`researcher_agent.py`)
- Handles `review`, `compare`.
- Deep legal research: document review, statute/case-law comparisons, structured findings.
- **Tools bound:** `define_legal_term`, `search_bare_acts` (fetches statute text before citing it).

### 3.4 Draft (`draft_agent.py`)
- Handles `draft`.
- Legal notices, agreements, letters, petitions. Replies support **PDF / DOCX / TXT export** and the HITL field-filling wizard.
- **Tools bound:** `list_legal_templates`, `get_legal_template` (fetches the matching template structure first).

### 3.5 Document Creator (`document_creator_agent.py`)
- Handles `document`.
- Structured documents: RTI applications, complaints, agreements, formal notices.
- **Tools bound:** `list_legal_templates`, `get_legal_template`.

### 3.6 Email (`email_agent.py`)
- Handles `email`.
- Professional legal emails (client communication, notices, follow-ups); can be sent via SMTP or exported.
- Deterministic (no external data needed) — plain LLM generation.

### 3.7 Lawyer Finder (`lawyer_finder_agent.py`)
- Handles `find_lawyer`.
- Pulls the **live lawyer directory from Neon Postgres** (14 seeded profiles with experience, fees, ratings, reviews) with a demo-listing fallback.
- Presents lawyers in a Markdown table and points users to the **💬 Live Chat with Lawyer** button.
- **Tools bound:** `list_lawyers`, `query_lawyer_database` — the model decides whether to browse the whole directory or run a filtered SQL query.

### 3.8 DB Chat (`db_chat_agent.py`) — Text-to-SQL
- Handles `db_query` (e.g. *"cheapest family lawyer in Delhi"*, *"average fees in Mumbai"*).
- **Agentic flow:** the LLM reads the schema, writes a SELECT and calls the `query_lawyer_database` tool itself → the tool validates + executes on Neon → rows return as a ToolMessage → the LLM formats a Markdown answer. If the model never calls the tool (or tool-calling is unavailable), it degrades to the classic deterministic pipeline (LLM writes SQL → validate → execute → format).
- Safety layers: SELECT/WITH only, forbidden-keyword rejection, comment stripping, single statement, `LIMIT ≤ 25`, `READ ONLY` transaction, 10s statement timeout.
- The **executed SQL is sent to the UI** as a dedicated `sql` SSE event and rendered in a code card (same contract in both paths — the agent lifts the SQL from the tool's ToolMessage payload).
- Error replies are flagged `cache_error` so failures are **never cached**.

### 3.9 The Agentic Tool-Binding Pattern — how to add any new use case

This is the canonical pattern of the project. Follow these steps for
every new feature so it plugs into the pipeline identically.

**The loop** (implemented once in `agents/tool_loop_runner.py`):

```
StateGraph(ToolLoopState)
  START ─► agent (LLM with bind_tools)
              │
              ├─ has tool_calls? ─► tools (ToolNode executes @tool fns)
              │                          │
              │◄─────────────────────────┘  (results fed back as ToolMessage)
              └─ no tool_calls ─► END  (final text reply)
```

**Step-by-step recipe:**

1. **Connector** — build the capability in `connectors/<name>.py` and
   `register_connector(...)` it (shows up in `/connectors` UI panel).
2. **Tool wrapper** — in `agents/agent_tools.py` add an `@tool` function that
   calls the connector. Rules:
   - Return a **compact JSON string** (models read tool output as text).
   - Keep docstrings descriptive — they become the tool description the
     LLM sees when deciding whether to call it.
   - If the result must appear in the UI pipeline (like executed SQL),
     include it in the returned JSON — the agent reads it back from
     `run_agent_with_tools()["tool_payloads"][tool_name]` (never use
     globals/thread-locals: ToolNode executes tools on worker threads).
   - Add the tool to the relevant tool-set list
     (`RESEARCH_TOOLS`, `LAWYER_TOOLS`, `DB_TOOLS`, `TEMPLATE_TOOLS`…).
3. **Agent** — create `agents/<name>.py`:
   ```python
   from agents.tool_loop_runner import run_agent_with_tools
   from agents.base import AgentState, register_agent
   from agents.agent_tools import MY_TOOLS

   def my_agent_generate(state: AgentState, config) -> dict:
       system = MY_SYSTEM_PROMPT  # + inject analysis/profile/memory/RAG from state
       result = run_agent_with_tools(system, state["messages"], MY_TOOLS, config)
       return {
           "reply": result["reply"],
           "active_agent": "my_agent",
           "agent_metadata": {"my_agent": {
               "model": result["model"],
               "agentic": result["agentic"],
               "tools_used": [t["tool"] for t in result["tool_trace"]],
           }},
       }

   register_agent("my_agent", description="…", handles=["my_intent"])
   ```
   Tell the model in the system prompt WHEN to use each tool; it decides
   the arguments itself.
4. **Wire into the graph** — in `agents/multi_agent_graph.py`: `add_node`, add it to
   the orchestrator's conditional-edge map and to the END-edge loop. In
   `agents/orchestrator_agent.py`: add the intent to `ORCHESTRATOR_PROMPT`
   routing rules and to `INTENT_AGENT_MAP`.
5. **UI surfacing (optional)** — `main.py` already forwards
   `agent_metadata`; add an SSE branch if the feature needs its own
   card (see the `sql` event for `db_chat`).

**Robustness guarantees built into `run_agent_with_tools`:**
- 429/rate-limit → automatic retry through `FALLBACK_MODELS` chain.
- Model without tool-calling support → graceful degradation to plain
  `invoke_text` generation (user still gets an answer).
- Tool exceptions inside the loop → returned to the model as error
  messages (`handle_tool_errors=True`), never crash the graph.
- Loop capped at `MAX_TOOL_ITERATIONS` round-trips (`recursion_limit`).

---

## 4. Memory system (7 layers + profile)

Source: `services/memory_service.py`. Every turn loads all layers into the prompt and writes back after the reply.

| Layer | Store | Purpose |
| --- | --- | --- |
| In-memory | Process RAM | Hot conversation buffer |
| Short-term | Redis | Recent turns per session |
| User thread | MongoDB | Full conversation history per journey |
| Long-term | MongoDB | Extracted durable facts |
| Semantic | Qdrant | Vector-recall of past interactions |
| Episodic | MongoDB | Summaries of past conversations (episodes) |
| Procedural | MongoDB | User preferences (language, tone, format) |

**User profile extraction:** names, emails, phones and facts are mined from the user's **actual message** (not the generated title) and injected into every agent prompt — so *"who am I?"* returns *"Hi Rahul!"*.

**Context compression:** old messages are summarized automatically to fit model context (`Compressed N old messages into summary, kept M recent`).

---

## 5. Caching system

Source: `services/cache_service.py`.

| Tier | Key | Store | TTL | Status |
| --- | --- | --- | --- | --- |
| **Exact-match** | SHA-256 of normalized prompt | Redis + in-memory | 6 h | Always on |
| **Semantic** | Embedding cosine similarity | Qdrant + Redis | configurable | **Off by default** — `SEMANTIC_CACHE_ENABLED=true` to enable |

Rules implemented:
- **Personal-query bypass** — queries like *"who am I?"*, *"what's my name?"* skip both lookup and store so memory (not a stale cache) answers.
- **Kill-switch** — when semantic cache is disabled the pipeline shows an honest `skip` step: `Semantic cache disabled (SEMANTIC_CACHE_ENABLED=false)`.
- **Error replies are never cached** (db_chat failures flagged `cache_error`).
- UI shows read/write status of both tiers in the pipeline panel.

---

## 6. RAG — document upload & retrieval

Endpoint `POST /documents/upload` (max 5 MB): PDF · DOCX · TXT · MD · CSV · images.

Pipeline (fully streamed to the UI): receive → validate → parse (image OCR via vision model) → chunk → MongoDB GridFS (original) → embed chunks (`nomic-embed-text-v1.5`, FastEmbed fallback for serverless) → index in Qdrant.

At query time the top hits are retrieved and injected into the agent prompt; the UI shows the retrieval report and file hits.

---

## 7. Lawyer directory + real-time chat

### 7.1 Neon Postgres directory
- Tables: `lawyers` (14 seeded profiles: specialisation, city, experience, bar ID, fees, rating, reviews, languages, profile) and `lawyer_reviews` (29 seeded reviews).
- Seed script: `python scripts/seed_lawyer_directory.py` (`--force` to truncate and reseed).
- Served to the UI via `GET /lawyers`.

### 7.2 Lawyer Connect (WebSocket)
Source: `websocket/lawyer_connect.py` + `/lawyer/rooms` REST endpoints.

Flow: user clicks **💬 Lawyer Chat** (header) or **💬 Live Chat with Lawyer** (below any lawyer-finder reply) → directory panel → pick a lawyer → room created (`POST /lawyer/rooms`) → WebSocket `/ws/lawyer/user/{room_id}` connects.

**Demo mode:** until a real lawyer client joins, the server simulates replies in the lawyer's voice — a personalized greeting first, then rotating professional responses (each tagged *simulated demo reply*).

> Vercel serverless does not hold WebSocket connections — live chat works on WebSocket-capable hosts (e.g. local `uvicorn`). The UI degrades gracefully with a clear message.

---

## 8. Frontend features

React + Vite + **Tailwind CSS only** (`src/App.jsx`), deployed at https://legal-assist-agentic.vercel.app.

- **Agent Pipeline panel** — live step-by-step visualization: memory reads/writes, exact & semantic cache, RAG, orchestrator routing, specialist work, follow-ups, cache/memory saves
- **Intent classification chips** — intent · domain · complexity · route
- **🗄 Executed SQL card** — full SQL, rows fetched, tables, columns for db_chat answers
- **💬 Lawyer Chat panel** — directory, room creation, WebSocket chat, status pills, end session
- **HITL Draft-Fill wizard** — guided field-by-field personalization before download/email
- **Download & Send Email** actions for draft/document/email replies (PDF · DOCX · TXT)
- Markdown replies with tables, journey sidebar (rename/delete), auto titles, follow-up chips
- Memory viewer page (profile, stores, episodes, preferences, facts, files)
- Dark/light themes · guest mode (3 messages) · file uploads with progress pipeline

---

## 9. Models

| Purpose | Model |
| --- | --- |
| Chat / reasoning | `openai/gpt-oss-120b` |
| Fallback chain (429) | `openai/gpt-oss-20b` → `qwen/qwen3.6-27b` → `groq/compound-mini` |
| Embeddings | `nomic-embed-text-v1.5` (Groq) · FastEmbed local fallback |
| Vision / OCR | `qwen/qwen3.6-27b` |

All served via Groq with retry-safe invokes and automatic model fallback.

---

## 10. SSE event protocol (`/chat/stream/v2`)

| Event | Payload | Shown as |
| --- | --- | --- |
| `thinking` | current activity text | status line |
| `flow` | pipeline steps array | Agent Pipeline timeline |
| `memory` | layers + facts | Memory reads chips |
| `retrieval` | report + hits | RAG pill + files |
| `cache` / `cache_write` | tier reports | cache pills + detail |
| `agent_route` | routed agent + analysis | route chip |
| `analysis` | intent/domain/complexity | classification chips |
| `sql` | sql · row_count · columns · tables | Executed SQL card |
| `token` | streamed text | answer body |
| `followups` | suggested questions | follow-up chips |
| `memory_write` | write reports + title | Memory writes chips |
| `done` / `error` | model / detail | terminal state |

---

## 11. API reference (summary)

| Group | Endpoints |
| --- | --- |
| System | `GET /` · `GET /health` |
| Auth | `POST /auth/register` · `POST /auth/login` · `GET/PATCH /auth/me` |
| Journeys | `GET/POST /journeys` · `PATCH/DELETE /journeys/{id}` · `DELETE /journeys` |
| Chat | `POST /chat` · `/chat/stream` · `/chat/stream/v2` · `/chat/guest` |
| Memory | `GET /memory` · `GET/PUT/DELETE /memory/profile` · `GET /memory/episodes` · `GET /memory/preferences` |
| Documents | `POST /documents/upload` · `GET /documents` · `GET /documents/{id}/file` · `DELETE /documents/{id}` |
| Connectors | `GET /connectors` · `GET /connectors/{name}` |
| Lawyers | `GET /lawyers` |
| Lawyer chat | `POST/GET /lawyer/rooms` · `GET/DELETE /lawyer/rooms/{id}` · `GET /lawyer/my-rooms` · `WS /ws/lawyer/user/{room_id}` · `WS /ws/lawyer/lawyer/{room_id}` |
| Output | `POST /export/download` · `POST /email/send` |
| Admin | `GET /admin/system` · `/admin/agents` · `/admin/connectors` |

Full interactive docs: https://legal-assist-api.vercel.app/docs

---

## 12. Deployment

Both apps are deployed on **Vercel** under [satyam-jaysawals-projects](https://vercel.com/satyam-jaysawals-projects).

| App | Vercel project | Production alias |
| --- | --- | --- |
| Backend | `legal-assist-api` | https://legal-assist-api.vercel.app |
| Frontend | `legal-assist` | https://legal-assist-agentic.vercel.app |

Workflow (run from each app directory):

```powershell
git add -A; git commit -m "<message>"; git push
vercel --prod --yes
# backend only — re-alias after every deploy:
vercel alias set <deployment-url> legal-assist-api.vercel.app
```

The frontend reads `VITE_API_URL` (set in Vercel project settings) at build time.

---

## 13. Try it

Log in at https://legal-assist-agentic.vercel.app and try:

- *"What are my rights as a tenant in India?"* — assistant + memory + pipeline view
- *"Show lawyers in Mumbai with 10+ years experience"* — lawyer_finder + 💬 Live Chat button
- *"List the top 5 highest rated lawyers from the database"* — db_chat + 🗄 Executed SQL card
- *"Average fees of criminal lawyers in Delhi"* — text-to-SQL aggregation
- Upload a PDF and ask about it — RAG pipeline
- *"Draft a rent notice to my landlord"* — draft agent + HITL wizard + PDF/DOCX export
