import json
import os
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from graph import DEFAULT_ANALYSIS, build_graph, latest_user_text, stream_graph
from memory import layer_status, load_all, save_all

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
load_dotenv(ROOT.parent / "legal_assist" / ".env")

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

app = FastAPI(title="Legal AI Assistant")

extra_origins = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", "").split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        *extra_origins,
    ],
    allow_origin_regex=r"https://.*\.vercel\.app",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatMessage(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    session_id: str = ""
    user_id: str = ""


class ChatResponse(BaseModel):
    reply: str
    model: str
    analysis: dict
    session_id: str
    user_id: str
    memory: dict


def require_key() -> str:
    if not GROQ_API_KEY:
        raise HTTPException(status_code=500, detail="GROQ_API_KEY is not configured")
    return GROQ_API_KEY


def cleaned_messages(req: ChatRequest) -> list[dict[str, str]]:
    messages = [
        {"role": item.role, "content": item.content.strip()}
        for item in req.messages
        if item.content.strip()
    ]
    if not messages:
        raise HTTPException(status_code=400, detail="messages cannot be empty")
    return messages


def ids_from(req: ChatRequest) -> tuple[str, str]:
    session_id = (req.session_id or "").strip() or str(uuid4())
    user_id = (req.user_id or "").strip() or session_id
    return session_id, user_id


def sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@app.get("/")
def root():
    return {"service": "legal_assist_backend", "ok": True}


@app.get("/health")
def health():
    return {
        "ok": True,
        "provider": "groq",
        "model": GROQ_MODEL,
        "configured": bool(GROQ_API_KEY),
        "stack": {
            "langchain": True,
            "langgraph": True,
            "streaming": "langgraph.stream",
            "stream_mode": ["updates", "messages"],
            "stream_version": "v2",
            "query_analyser": True,
            "memory": ["in_memory", "short_term_redis", "long_term_mongodb"],
        },
        "memory": layer_status(),
    }


@app.get("/session/{session_id}")
def get_session(session_id: str, user_id: str = ""):
    loaded = load_all(session_id, user_id or session_id, [], "")
    return {
        "session_id": session_id,
        "user_id": user_id or session_id,
        "messages": loaded["history"],
        "memory": {"layers": loaded["layers"], "facts": loaded["facts"]},
    }


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    api_key = require_key()
    incoming = cleaned_messages(req)
    session_id, user_id = ids_from(req)
    query = latest_user_text(incoming)
    loaded = load_all(session_id, user_id, incoming, query)
    try:
        result = build_graph(api_key, GROQ_MODEL).invoke(
            {
                "messages": loaded["history"],
                "analysis": None,
                "reply": "",
                "memory_notes": loaded["notes"],
            }
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"LangGraph error: {exc}") from exc

    reply = (result.get("reply") or "").strip()
    if not reply:
        raise HTTPException(status_code=502, detail="Groq returned an empty reply")

    analysis = result.get("analysis") or DEFAULT_ANALYSIS
    stored = loaded["history"] + [{"role": "assistant", "content": reply}]
    writes = save_all(session_id, user_id, stored, query, reply, analysis)
    return ChatResponse(
        reply=reply,
        model=GROQ_MODEL,
        analysis=analysis,
        session_id=session_id,
        user_id=user_id,
        memory={"layers": loaded["layers"], "writes": writes, "facts": loaded["facts"]},
    )


@app.post("/chat/stream")
def chat_stream(req: ChatRequest):
    api_key = require_key()
    incoming = cleaned_messages(req)
    session_id, user_id = ids_from(req)
    query = latest_user_text(incoming)
    loaded = load_all(session_id, user_id, incoming, query)

    def generate():
        try:
            yield sse(
                {
                    "type": "memory",
                    "session_id": session_id,
                    "user_id": user_id,
                    "layers": loaded["layers"],
                    "facts": loaded["facts"],
                }
            )
            analysis = None
            reply_parts: list[str] = []
            for event in stream_graph(
                loaded["history"],
                api_key,
                GROQ_MODEL,
                memory_notes=loaded["notes"],
            ):
                if event.get("type") == "analysis":
                    analysis = event.get("analysis")
                if event.get("type") == "token":
                    reply_parts.append(event.get("content") or "")
                yield sse(event)
            reply = "".join(reply_parts).strip()
            if reply:
                stored = loaded["history"] + [{"role": "assistant", "content": reply}]
                writes = save_all(session_id, user_id, stored, query, reply, analysis)
                yield sse({"type": "memory_write", "writes": writes})
        except Exception as exc:
            yield sse({"type": "error", "detail": f"LangGraph error: {exc}"})

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
