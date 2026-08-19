import os
import tempfile
from pathlib import Path
from typing import Any

import hashlib
import logging
import math
import re

from dotenv import load_dotenv
from groq import Groq

logger = logging.getLogger("legal_assist.embeddings")

load_dotenv(Path(__file__).resolve().parent / ".env")
load_dotenv(Path(__file__).resolve().parent.parent / "legal_assist" / ".env")

_CACHE_DIR = os.getenv("FASTEMBED_CACHE_PATH") or os.path.join(tempfile.gettempdir(), "legal_assist_fastembed")
os.environ.setdefault("FASTEMBED_CACHE_PATH", _CACHE_DIR)
os.environ.setdefault("HF_HOME", os.path.join(_CACHE_DIR, "hf"))
os.environ.setdefault("HUGGINGFACE_HUB_CACHE", os.path.join(_CACHE_DIR, "hub"))

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

        os.makedirs(_CACHE_DIR, exist_ok=True)
        _fastembed = TextEmbedding(model_name=FASTEMBED_MODEL, cache_dir=_CACHE_DIR)
    return [vec.tolist() for vec in _fastembed.embed(texts)]


def _hash_embed(texts: list[str]) -> list[list[float]]:
    """Deterministic bag-of-words feature-hashing embeddings.

    Zero-dependency fallback for serverless environments where Groq
    embeddings are unavailable and FastEmbed cannot write model files
    to disk.  Cosine similarity over these vectors approximates token
    overlap — good enough for semantic cache / semantic memory.
    """
    vectors: list[list[float]] = []
    for text in texts:
        vec = [0.0] * EMBED_DIM
        raw = re.findall(r"[a-z0-9]+", (text or "").lower())
        # light suffix-stripping so "filing"/"filed"/"files" ≈ "file"
        tokens = [
            t[:-3] if len(t) > 5 and t.endswith("ing") else
            t[:-2] if len(t) > 4 and t.endswith("ed") else
            t[:-2] if len(t) > 3 and t.endswith("ly") else
            t[:-1] if len(t) > 3 and t.endswith("s") else t
            for t in raw
        ]
        grams = tokens + [f"{a}_{b}" for a, b in zip(tokens, tokens[1:])]
        for gram in grams:
            digest = hashlib.md5(gram.encode("utf-8")).digest()
            idx = int.from_bytes(digest[:4], "little") % EMBED_DIM
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vec[idx] += sign
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        vectors.append([v / norm for v in vec])
    return vectors


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
            logger.warning("Groq embeddings unavailable (%s); trying FastEmbed", exc)
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
        logger.warning("FastEmbed unavailable (%s); using deterministic hash embeddings", exc)
    vectors = _hash_embed(prepared)
    _provider = "hash"
    report.update(
        provider="hash",
        model="hash-bow-768",
        status="hit",
        detail=f"Hash fallback embeddings (serverless-safe) · {len(vectors)} vector(s)",
    )
    return vectors, report
