import json
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from auth import current_user, login_user, make_token, register_user, update_user
from graph import (
    DEFAULT_ANALYSIS,
    build_graph,
    latest_user_text,
    stream_graph,
    suggest_followups,
    suggest_title,
)
from journeys import create_journey, get_journey, list_journeys, rename_journey
from memory import layer_status, list_user_facts, load_all, save_all

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


class AuthPayload(BaseModel):
    email: str
    password: str
    name: str = ""


class ProfileUpdate(BaseModel):
    name: str


class JourneyCreate(BaseModel):
    title: str = ""


class JourneyRename(BaseModel):
    title: str


class ChatMessage(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    journey_id: str = ""
    session_id: str = ""


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


def resolve_journey(user: dict, journey_id: str) -> dict:
    journey_id = (journey_id or "").strip()
    if journey_id:
        return get_journey(user["user_id"], journey_id)
    return create_journey(user["user_id"])


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
            "query_analyser": True,
            "auth": "mongodb",
            "memory": ["in_memory", "short_term_redis", "user_thread_mongo", "long_term_mongodb"],
        },
        "memory": layer_status(),
    }


@app.post("/auth/register")
def auth_register(payload: AuthPayload):
    user = register_user(payload.email, payload.password, payload.name)
    token = make_token(user["user_id"], user["email"])
    journey = create_journey(user["user_id"], "New chat")
    return {"token": token, "user": user, "journey": journey}


@app.post("/auth/login")
def auth_login(payload: AuthPayload):
    user = login_user(payload.email, payload.password)
    token = make_token(user["user_id"], user["email"])
    journeys = list_journeys(user["user_id"])
    journey = journeys[0] if journeys else create_journey(user["user_id"], "New chat")
    return {"token": token, "user": user, "journey": journey, "journeys": journeys or [journey]}


@app.get("/auth/me")
def auth_me(user: dict = Depends(current_user)):
    journeys = list_journeys(user["user_id"])
    return {"user": user, "journeys": journeys, "journey_count": len(journeys)}


@app.patch("/auth/me")
def auth_update(payload: ProfileUpdate, user: dict = Depends(current_user)):
    return {"user": update_user(user["user_id"], payload.name)}


@app.get("/journeys")
def journeys_list(user: dict = Depends(current_user)):
    return {"journeys": list_journeys(user["user_id"])}


@app.post("/journeys")
def journeys_create(payload: JourneyCreate, user: dict = Depends(current_user)):
    return create_journey(user["user_id"], payload.title)


@app.patch("/journeys/{journey_id}")
def journeys_rename(journey_id: str, payload: JourneyRename, user: dict = Depends(current_user)):
    return rename_journey(user["user_id"], journey_id, payload.title)


@app.get("/memory")
def memory_detail(journey_id: str = "", user: dict = Depends(current_user)):
    journey_id = (journey_id or "").strip()
    loaded = None
    if journey_id:
        loaded = load_all(user["user_id"], journey_id, [], "")
    return {
        "user_id": user["user_id"],
        "journey_id": journey_id,
        "stores": layer_status(),
        "layers": loaded["layers"] if loaded else [],
        "facts": list_user_facts(user["user_id"]),
        "recalled": loaded["facts"] if loaded else [],
        "thread": loaded["history"] if loaded else [],
    }


@app.get("/journeys/{journey_id}")
def journeys_get(journey_id: str, user: dict = Depends(current_user)):
    journey = get_journey(user["user_id"], journey_id)
    loaded = load_all(user["user_id"], journey_id, journey.get("messages") or [], "")
    journey["messages"] = loaded["history"]
    journey["memory"] = {"layers": loaded["layers"], "facts": loaded["facts"]}
    return journey


@app.post("/chat/stream")
def chat_stream(req: ChatRequest, user: dict = Depends(current_user)):
    api_key = require_key()
    incoming = cleaned_messages(req)
    journey = resolve_journey(user, req.journey_id or req.session_id)
    journey_id = journey["journey_id"]
    user_id = user["user_id"]
    query = latest_user_text(incoming)
    loaded = load_all(user_id, journey_id, incoming, query)

    def generate():
        try:
            yield sse(
                {
                    "type": "memory",
                    "user_id": user_id,
                    "journey_id": journey_id,
                    "session_id": journey_id,
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
                title = ""
                try:
                    title = suggest_title(query, reply, api_key, GROQ_MODEL)
                except Exception:
                    title = query[:60]
                stored = loaded["history"] + [{"role": "assistant", "content": reply}]
                writes = save_all(journey_id, user_id, stored, title or query, reply, analysis)
                yield sse(
                    {
                        "type": "memory_write",
                        "writes": writes,
                        "journey_id": journey_id,
                        "session_id": journey_id,
                        "title": title,
                    }
                )
                try:
                    followups = suggest_followups(query, reply, api_key, GROQ_MODEL)
                except Exception:
                    followups = []
                if followups:
                    yield sse({"type": "followups", "questions": followups})
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


@app.post("/chat")
def chat(req: ChatRequest, user: dict = Depends(current_user)):
    api_key = require_key()
    incoming = cleaned_messages(req)
    journey = resolve_journey(user, req.journey_id or req.session_id)
    journey_id = journey["journey_id"]
    user_id = user["user_id"]
    query = latest_user_text(incoming)
    loaded = load_all(user_id, journey_id, incoming, query)
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
    writes = save_all(journey_id, user_id, stored, query, reply, analysis)
    return {
        "reply": reply,
        "model": GROQ_MODEL,
        "analysis": analysis,
        "journey_id": journey_id,
        "session_id": journey_id,
        "user_id": user_id,
        "memory": {"layers": loaded["layers"], "writes": writes, "facts": loaded["facts"]},
    }
