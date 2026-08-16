import json
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from graph import DEFAULT_ANALYSIS, analyse_query, build_graph, stream_answer

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


class ChatResponse(BaseModel):
    reply: str
    model: str
    analysis: dict


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
            "streaming": True,
            "query_analyser": True,
        },
    }


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    api_key = require_key()
    messages = cleaned_messages(req)
    try:
        result = build_graph(api_key, GROQ_MODEL).invoke(
            {"messages": messages, "analysis": None, "reply": ""}
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"LangGraph error: {exc}") from exc

    reply = (result.get("reply") or "").strip()
    if not reply:
        raise HTTPException(status_code=502, detail="Groq returned an empty reply")

    return ChatResponse(
        reply=reply,
        model=GROQ_MODEL,
        analysis=result.get("analysis") or DEFAULT_ANALYSIS,
    )


@app.post("/chat/stream")
def chat_stream(req: ChatRequest):
    api_key = require_key()
    messages = cleaned_messages(req)

    def generate():
        try:
            analysis = analyse_query(messages, api_key, GROQ_MODEL)
            yield sse({"type": "analysis", "analysis": analysis, "model": GROQ_MODEL})
            reply_parts: list[str] = []
            for token in stream_answer(messages, analysis, api_key, GROQ_MODEL):
                reply_parts.append(token)
                yield sse({"type": "token", "content": token})
            if not "".join(reply_parts).strip():
                yield sse({"type": "error", "detail": "Groq returned an empty reply"})
                return
            yield sse({"type": "done", "model": GROQ_MODEL})
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
