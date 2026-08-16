import hashlib
import json
import os
import re
from datetime import datetime, timezone
from typing import Any

from memory import get_redis

PROMPT_CACHE_TTL = int(os.getenv("PROMPT_CACHE_TTL", "21600"))
_local: dict[str, dict[str, Any]] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_prompt(query: str) -> str:
    return re.sub(r"\s+", " ", (query or "").strip().lower())


def prompt_cache_id(query: str, model: str) -> str:
    raw = f"{model}|{normalize_prompt(query)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def prompt_cache_key(query: str, model: str) -> str:
    return f"legal_assist:pcache:{prompt_cache_id(query, model)}"


def cache_status() -> dict[str, Any]:
    client = get_redis()
    return {
        "ok": client is not None,
        "store": "redis+memory",
        "ttl_seconds": PROMPT_CACHE_TTL,
        "local_entries": len(_local),
    }


def get_prompt_cache(query: str, model: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    cache_id = prompt_cache_id(query, model)
    key = prompt_cache_key(query, model)
    report = {
        "name": "prompt_cache",
        "label": "Prompt cache",
        "used": False,
        "status": "miss",
        "store": None,
        "key": key,
        "cache_id": cache_id,
        "when": _now(),
        "detail": "No cached answer for this prompt",
    }
    local = _local.get(key)
    if local:
        report.update(
            used=True,
            status="hit",
            store="in_memory",
            detail="Hit in-memory prompt cache",
        )
        return local, report
    client = get_redis()
    if client is None:
        report["detail"] = "Prompt cache miss (Redis unavailable, no local hit)"
        return None, report
    try:
        raw = client.get(key)
        if not raw:
            return None, report
        payload = json.loads(raw)
        _local[key] = payload
        report.update(
            used=True,
            status="hit",
            store="redis",
            detail="Hit Redis prompt cache",
        )
        return payload, report
    except Exception as exc:
        report["status"] = "error"
        report["detail"] = str(exc)
        return None, report


def set_prompt_cache(query: str, model: str, payload: dict[str, Any]) -> dict[str, Any]:
    key = prompt_cache_key(query, model)
    body = {
        "query": query,
        "model": model,
        "reply": payload.get("reply") or "",
        "analysis": payload.get("analysis") or {},
        "followups": payload.get("followups") or [],
        "title": payload.get("title") or "",
        "cached_at": _now(),
    }
    _local[key] = body
    report = {
        "name": "prompt_cache",
        "label": "Prompt cache",
        "wrote": True,
        "store": "in_memory",
        "when": _now(),
        "detail": "Wrote in-memory prompt cache",
    }
    client = get_redis()
    if client is None:
        return report
    try:
        client.setex(key, PROMPT_CACHE_TTL, json.dumps(body))
        report["store"] = "redis"
        report["detail"] = f"Wrote Redis prompt cache, TTL {PROMPT_CACHE_TTL}s"
        return report
    except Exception as exc:
        report["detail"] = f"Local write ok; Redis failed: {exc}"
        return report
