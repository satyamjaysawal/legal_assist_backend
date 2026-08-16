import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from groq import Groq

load_dotenv(Path(__file__).resolve().parent / ".env")
load_dotenv(Path(__file__).resolve().parent.parent / "legal_assist" / ".env")

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_EMBED_MODEL = os.getenv("GROQ_EMBED_MODEL", "nomic-embed-text-v1.5")
FASTEMBED_MODEL = os.getenv("FASTEMBED_MODEL", "nomic-ai/nomic-embed-text-v1.5")
EMBED_DIM = int(os.getenv("EMBED_DIM", "768"))

_fastembed = None
_provider = ""
_last_error = ""


def _prefixed(text: str, kind: str) -> str:
    clean = (text or "").strip()
    if kind == "query":
        if clean.lower().startswith("search_query:"):
            return clean
        return f"search_query: {clean}"
    if clean.lower().startswith("search_document:"):
        return clean
    return f"search_document: {clean}"


def embed_status() -> dict[str, Any]:
    return {
        "ok": bool(GROQ_API_KEY) or _fastembed is not None,
        "model": GROQ_EMBED_MODEL,
        "fastembed_model": FASTEMBED_MODEL,
        "dim": EMBED_DIM,
        "provider": _provider or "unset",
        "error": _last_error,
    }


def _groq_embed(texts: list[str]) -> list[list[float]]:
    client = Groq(api_key=GROQ_API_KEY)
    result = client.embeddings.create(model=GROQ_EMBED_MODEL, input=texts)
    vectors = [item.embedding for item in result.data]
    if not vectors or len(vectors[0]) != EMBED_DIM:
        raise RuntimeError(f"Unexpected embedding size from Groq: {len(vectors[0]) if vectors else 0}")
    return vectors


def _fastembed_embed(texts: list[str]) -> list[list[float]]:
    global _fastembed
    if _fastembed is None:
        from fastembed import TextEmbedding

        _fastembed = TextEmbedding(model_name=FASTEMBED_MODEL)
    return [vec.tolist() for vec in _fastembed.embed(texts)]


def embed_texts(texts: list[str], kind: str = "document") -> tuple[list[list[float]], dict[str, Any]]:
    global _provider, _last_error
    prepared = [_prefixed(text, kind) for text in texts if (text or "").strip()]
    if not prepared:
        raise ValueError("No text to embed")
    report = {
        "name": "embeddings",
        "label": "Embeddings",
        "model": GROQ_EMBED_MODEL,
        "kind": kind,
        "count": len(prepared),
        "dim": EMBED_DIM,
        "provider": "",
        "status": "miss",
        "detail": "",
    }
    if GROQ_API_KEY:
        try:
            vectors = _groq_embed(prepared)
            _provider = "groq"
            _last_error = ""
            report.update(
                provider="groq",
                status="hit",
                detail=f"Groq {GROQ_EMBED_MODEL} · {len(vectors)} vector(s)",
            )
            return vectors, report
        except Exception as exc:
            _last_error = str(exc)
            report["detail"] = f"Groq embeddings unavailable ({exc}). Falling back to FastEmbed."
    try:
        vectors = _fastembed_embed(prepared)
        _provider = "fastembed"
        report.update(
            provider="fastembed",
            model=FASTEMBED_MODEL,
            status="hit",
            detail=f"FastEmbed {FASTEMBED_MODEL} · {len(vectors)} vector(s)",
        )
        return vectors, report
    except Exception as exc:
        _last_error = str(exc)
        report.update(status="error", detail=str(exc), provider="none")
        raise RuntimeError(f"Could not embed text: {exc}") from exc
