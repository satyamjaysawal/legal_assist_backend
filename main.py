import json
import os
import uuid
from pathlib import Path

from dotenv import load_dotenv

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
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
from cache import get_prompt_cache, set_prompt_cache
from docs import MAX_UPLOAD_BYTES, MAX_UPLOAD_MB, chunk_text, parse_file
from embeddings import embed_status
from files import files_status, get_original_file, store_original_file
from memory import layer_status, list_user_facts, load_all, save_all
from vectordb import (
    delete_doc,
    format_hits,
    get_doc,
    hits_fingerprint,
    ingest_document,
    list_docs,
    qdrant_status,
    search_docs,
)

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
            "prompt_cache": True,
            "rag": True,
            "uploads": ["pdf", "docx", "text", "image"],
            "embed_model": os.getenv("GROQ_EMBED_MODEL", "nomic-embed-text-v1.5"),
            "file_store": "mongodb_gridfs",
            "max_upload_mb": MAX_UPLOAD_MB,
        },
        "memory": layer_status(),
        "qdrant": qdrant_status(),
        "embeddings": embed_status(),
        "files": files_status(),
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
        "documents": list_docs(user["user_id"], journey_id) if journey_id else list_docs(user["user_id"]),
        "qdrant": qdrant_status(),
        "embeddings": embed_status(),
        "files": files_status(),
    }


@app.post("/documents/upload")
async def documents_upload(
    file: UploadFile = File(...),
    journey_id: str = Form(""),
    user: dict = Depends(current_user),
):
    journey = resolve_journey(user, journey_id)
    filename = file.filename or "upload"
    content_type = file.content_type or ""
    data = await file.read()
    user_id = user["user_id"]
    journey_id = journey["journey_id"]

    def generate():
        flow = []

        def step(name, status, detail=""):
            item = {"name": name, "status": status, "detail": detail}
            flow.append(item)
            return sse({"type": "flow", "steps": flow[:], "current": name})

        try:
            yield sse({"type": "thinking", "text": f"Received {filename}…"})
            yield sse(
                {
                    "type": "file",
                    "filename": filename,
                    "bytes": len(data),
                    "content_type": content_type,
                    "max_bytes": MAX_UPLOAD_BYTES,
                }
            )
            yield step("receive", "done", f"{filename} · {len(data)} bytes")

            yield sse({"type": "thinking", "text": f"Checking size (max {MAX_UPLOAD_MB} MB)…"})
            if len(data) > MAX_UPLOAD_BYTES:
                yield step("validate", "error", f"Larger than {MAX_UPLOAD_MB} MB")
                yield sse({"type": "error", "detail": f"File is larger than {MAX_UPLOAD_MB} MB"})
                return
            yield step("validate", "done", f"Within {MAX_UPLOAD_MB} MB · {content_type or 'unknown type'}")

            yield sse({"type": "thinking", "text": f"Parsing {filename}…"})
            yield step("parse", "running", "Extracting text")
            parsed = parse_file(filename, content_type, data)
            yield step("parse", "done", f"{parsed['kind']} · {parsed['chars']} characters")

            yield sse({"type": "thinking", "text": "Splitting into chunks…"})
            chunks = chunk_text(parsed["text"])
            if not chunks:
                yield step("chunk", "error", "No text chunks")
                yield sse({"type": "error", "detail": "No chunks produced from file"})
                return
            yield step("chunk", "done", f"{len(chunks)} chunk(s)")

            doc_id = str(uuid.uuid4())
            yield sse({"type": "thinking", "text": "Uploading original file to MongoDB…"})
            yield step("mongodb", "running", "GridFS write")
            file_meta = store_original_file(
                user_id=user_id,
                journey_id=journey_id,
                doc_id=doc_id,
                filename=filename,
                content_type=content_type,
                data=data,
            )
            yield step("mongodb", "done", file_meta["detail"])
            yield sse({"type": "mongo", "report": file_meta})

            yield sse({"type": "thinking", "text": "Embedding chunks…"})
            yield step("embed", "running", "nomic-embed-text-v1.5 / fallback")
            stored = ingest_document(
                user_id,
                journey_id,
                parsed,
                chunks,
                doc_id=doc_id,
                file_meta=file_meta,
            )
            yield step("embed", "done", f"{stored.get('embed_provider') or stored.get('embed_model')} · {stored['chunks']} vector(s)")
            yield step("qdrant", "done", f"Indexed in {stored.get('collection') or 'Qdrant'}")
            yield sse({"type": "document", "document": stored, "journey_id": journey_id})
            yield sse({"type": "thinking", "text": "File stored in MongoDB and indexed."})
            yield sse({"type": "done", "document": stored, "journey_id": journey_id})
        except ValueError as exc:
            yield sse({"type": "error", "detail": str(exc)})
        except Exception as exc:
            yield sse({"type": "error", "detail": f"Could not ingest file: {exc}"})

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/documents")
def documents_list(journey_id: str = "", user: dict = Depends(current_user)):
    return {
        "documents": list_docs(user["user_id"], journey_id),
        "qdrant": qdrant_status(),
        "embeddings": embed_status(),
        "files": files_status(),
        "max_upload_mb": MAX_UPLOAD_MB,
    }


@app.get("/documents/{doc_id}/file")
def documents_download(doc_id: str, user: dict = Depends(current_user)):
    meta = get_doc(user["user_id"], doc_id)
    if not meta or not meta.get("gridfs_id"):
        raise HTTPException(status_code=404, detail="Original file is not stored")
    try:
        grid_out, file_meta = get_original_file(user["user_id"], meta["gridfs_id"])
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    filename = file_meta["filename"].replace('"', "")
    return StreamingResponse(
        grid_out,
        media_type=file_meta["content_type"],
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.delete("/documents/{doc_id}")
def documents_delete(doc_id: str, user: dict = Depends(current_user)):
    return delete_doc(user["user_id"], doc_id)


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
            flow = []

            def step(name, status, detail=""):
                item = {"name": name, "status": status, "detail": detail}
                flow.append(item)
                return sse({"type": "flow", "steps": flow[:], "current": name})

            yield sse({"type": "thinking", "text": "Loading memory stores…"})
            yield step("memory", "done", "Loaded in-memory, Redis, thread, long-term")
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

            yield sse({"type": "thinking", "text": "Searching uploaded documents in Qdrant…"})
            hits, rag_report = search_docs(user_id, query, journey_id)
            rag_notes = format_hits(hits)
            rag_key = hits_fingerprint(hits)
            yield step(
                "rag",
                rag_report.get("status") or "miss",
                rag_report.get("detail") or "Document search",
            )
            yield sse({"type": "retrieval", "report": rag_report, "hits": hits})

            yield sse({"type": "thinking", "text": "Checking prompt cache…"})
            cached, cache_report = get_prompt_cache(query, GROQ_MODEL, rag_key)
            yield sse({"type": "cache", "report": cache_report})

            if cached and cached.get("reply"):
                yield step("prompt_cache", "hit", cache_report.get("detail") or "Cache hit")
                analysis = cached.get("analysis") or DEFAULT_ANALYSIS
                yield sse({"type": "thinking", "text": "Using cached analysis…"})
                yield step("analyser", "cached", analysis.get("summary") or "Cached analysis")
                yield sse({"type": "analysis", "analysis": analysis, "model": GROQ_MODEL, "cached": True})
                yield sse({"type": "thinking", "text": "Replaying cached answer…"})
                yield step("generate", "cached", "Answer served from prompt cache")
                yield sse({"type": "token", "content": cached["reply"]})
                yield sse({"type": "done", "model": GROQ_MODEL, "cached": True})
                stored = loaded["history"] + [{"role": "assistant", "content": cached["reply"]}]
                writes = save_all(
                    journey_id,
                    user_id,
                    stored,
                    cached.get("title") or query,
                    cached["reply"],
                    analysis,
                )
                yield sse(
                    {
                        "type": "memory_write",
                        "writes": writes,
                        "journey_id": journey_id,
                        "session_id": journey_id,
                        "title": cached.get("title") or "",
                    }
                )
                if cached.get("followups"):
                    yield sse({"type": "followups", "questions": cached["followups"]})
                yield sse({"type": "thinking", "text": "Done (prompt cache)."})
                return

            yield step("prompt_cache", "miss", "No cached prompt, calling model")
            yield sse({"type": "thinking", "text": "Analysing the legal query…"})
            yield step("analyser", "running", "LangGraph analyse node")
            analysis = None
            reply_parts: list[str] = []
            for event in stream_graph(
                loaded["history"],
                api_key,
                GROQ_MODEL,
                memory_notes=loaded["notes"],
                rag_notes=rag_notes,
            ):
                if event.get("type") == "analysis":
                    analysis = event.get("analysis")
                    yield step("analyser", "done", (analysis or {}).get("summary") or "Analysed")
                    yield sse({"type": "thinking", "text": "Writing the answer…"})
                    yield step("generate", "running", "LangGraph generate node")
                if event.get("type") == "token":
                    reply_parts.append(event.get("content") or "")
                if event.get("type") == "done":
                    yield step("generate", "done", "Answer generated")
                yield sse(event)
            reply = "".join(reply_parts).strip()
            if reply:
                title = ""
                yield sse({"type": "thinking", "text": "Naming this chat…"})
                try:
                    title = suggest_title(query, reply, api_key, GROQ_MODEL)
                    yield step("title", "done", title)
                except Exception:
                    title = query[:60]
                    yield step("title", "done", title)
                followups: list[str] = []
                yield sse({"type": "thinking", "text": "Suggesting follow-up questions…"})
                try:
                    followups = suggest_followups(query, reply, api_key, GROQ_MODEL)
                    yield step("followups", "done", f"{len(followups)} questions")
                except Exception:
                    yield step("followups", "skip", "Could not suggest follow-ups")
                cache_write = set_prompt_cache(
                    query,
                    GROQ_MODEL,
                    {
                        "reply": reply,
                        "analysis": analysis or DEFAULT_ANALYSIS,
                        "followups": followups,
                        "title": title,
                    },
                    rag_key,
                )
                yield sse({"type": "cache_write", "report": cache_write})
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
                if followups:
                    yield sse({"type": "followups", "questions": followups})
                yield sse({"type": "thinking", "text": "Done."})
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
    hits, rag_report = search_docs(user_id, query, journey_id)
    try:
        result = build_graph(api_key, GROQ_MODEL).invoke(
            {
                "messages": loaded["history"],
                "analysis": None,
                "reply": "",
                "memory_notes": loaded["notes"],
                "rag_notes": format_hits(hits),
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
        "retrieval": {"report": rag_report, "hits": hits},
    }
