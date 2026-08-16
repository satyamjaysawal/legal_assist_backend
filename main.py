import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from groq import Groq
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
load_dotenv(ROOT.parent / "legal_assist" / ".env")

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

if not GROQ_API_KEY:
    raise RuntimeError("GROQ_API_KEY is missing. Set it in backend/.env")

client = Groq(api_key=GROQ_API_KEY)

SYSTEM_PROMPT = (
    "You are a legal AI assistant. Give clear, practical answers. "
    "You are not a lawyer and this is not formal legal advice."
)

app = FastAPI(title="Legal AI Assistant")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
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


@app.get("/health")
def health():
    return {"ok": True, "provider": "groq", "model": GROQ_MODEL}


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    if not req.messages:
        raise HTTPException(status_code=400, detail="messages cannot be empty")

    payload = [{"role": "system", "content": SYSTEM_PROMPT}] + [
        {"role": m.role, "content": m.content.strip()}
        for m in req.messages
        if m.content.strip()
    ]

    try:
        completion = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=payload,
            temperature=0.4,
            max_tokens=1024,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Groq error: {exc}") from exc

    reply = (completion.choices[0].message.content or "").strip()
    if not reply:
        raise HTTPException(status_code=502, detail="Groq returned an empty reply")

    return ChatResponse(reply=reply, model=GROQ_MODEL)
