import hashlib
import json
import math
import os
import re
from datetime import datetime, timezone
from typing import Any

import logging

from memory import get_redis

logger = logging.getLogger("legal_assist.cache")

PROMPT_CACHE_TTL = int(os.getenv("PROMPT_CACHE_TTL", "21600"))
SEMANTIC_CACHE_THRESHOLD = float(os.getenv("SEMANTIC_CACHE_THRESHOLD", "0.65"))
SEMANTIC_INDEX_KEY = "legal_assist:scache:index"
SEMANTIC_MAX_ENTRIES = int(os.getenv("SEMANTIC_MAX_ENTRIES", "300"))
_local: dict[str, dict[str, Any]] = {}
_sem_local: dict[str, dict[str, Any]] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_prompt(query: str) -> str:
    return re.sub(r"\s+", " ", (query or "").strip().lower())


def prompt_cache_id(query: str, model: str, extra: str = "") -> str:
    raw = f"{model}|{normalize_prompt(query)}|{extra or 'none'}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def prompt_cache_key(query: str, model: str, extra: str = "") -> str:
    return f"legal_assist:pcache:{prompt_cache_id(query, model, extra)}"


def cache_status() -> dict[str, Any]:
    client = get_redis()
    return {
        "ok": client is not None,
        "store": "redis+memory",
        "ttl_seconds": PROMPT_CACHE_TTL,
        "local_entries": len(_local),
    }


def get_prompt_cache(query: str, model: str, extra: str = "") -> tuple[dict[str, Any] | None, dict[str, Any]]:
    cache_id = prompt_cache_id(query, model, extra)
    key = prompt_cache_key(query, model, extra)
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
        logger.info("Exact cache HIT (in_memory) for %s", cache_id)
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
        logger.info("Exact cache HIT (redis) for %s", cache_id)
        return payload, report
    except Exception as exc:
        report["status"] = "error"
        report["detail"] = str(exc)
        return None, report


def set_prompt_cache(query: str, model: str, payload: dict[str, Any], extra: str = "") -> dict[str, Any]:
    key = prompt_cache_key(query, model, extra)
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


# ═══════════════════════════════════════════════════════════════
# SEMANTIC CACHE — embedding similarity over cached queries
# ═══════════════════════════════════════════════════════════════

def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _load_sem_index() -> dict[str, dict[str, Any]]:
    """Merge the local semantic index with the Redis-backed one."""
    merged = dict(_sem_local)
    client = get_redis()
    if client is None:
        return merged
    try:
        for entry_id, raw in (client.hgetall(SEMANTIC_INDEX_KEY) or {}).items():
            try:
                merged[entry_id] = json.loads(raw)
            except Exception:
                continue
    except Exception:
        pass
    return merged


def semantic_cache_lookup(query: str, model: str, extra: str = "") -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Find a cached answer whose query embedding is close to this query."""
    report: dict[str, Any] = {
        "name": "semantic_cache",
        "label": "Semantic cache",
        "used": False,
        "status": "miss",
        "threshold": SEMANTIC_CACHE_THRESHOLD,
        "when": _now(),
        "detail": "No semantically similar cached query",
    }
    index = _load_sem_index()
    if not index:
        report["detail"] = "Semantic index empty — first query stores it"
        return None, report
    try:
        from embeddings import embed_texts

        vectors, _emb = embed_texts([query], kind="query")
        qvec = vectors[0]
    except Exception as exc:
        report.update(status="skip", detail=f"Embeddings unavailable — semantic cache skipped ({exc})")
        return None, report

    best_id, best_score, best_entry = "", 0.0, None
    for entry_id, entry in index.items():
        if entry.get("model") != model:
            continue
        score = _cosine(qvec, entry.get("vector") or [])
        if score > best_score:
            best_id, best_score, best_entry = entry_id, score, entry

    report["best_similarity"] = round(best_score, 3)
    if best_entry is not None:
        report["matched_query"] = best_entry.get("query") or ""
    if best_entry is None or best_score < SEMANTIC_CACHE_THRESHOLD:
        report["detail"] = (
            f"Best similarity {best_score:.2f} < {SEMANTIC_CACHE_THRESHOLD:.2f} threshold"
            if best_entry
            else "No comparable cached query"
        )
        return None, report

    key = prompt_cache_key(best_entry.get("query") or query, model, extra)
    payload = _local.get(key)
    if payload is None:
        client = get_redis()
        if client is not None:
            try:
                raw = client.get(key)
                if raw:
                    payload = json.loads(raw)
                    _local[key] = payload
            except Exception:
                payload = None
    if payload is None:
        report["detail"] = f"Semantic hit ({best_score:.2f}) but cached payload expired"
        return None, report

    report.update(
        used=True,
        status="hit",
        store="redis+memory",
        entry_id=best_id,
        detail=f"{best_score:.0%} similar to cached query “{(best_entry.get('query') or '')[:60]}”",
    )
    return payload, report


def semantic_cache_store(query: str, model: str, extra: str = "") -> dict[str, Any]:
    """Embed this query and add it to the semantic cache index."""
    report: dict[str, Any] = {
        "name": "semantic_cache",
        "label": "Semantic cache",
        "wrote": False,
        "when": _now(),
        "detail": "",
    }
    try:
        from embeddings import embed_texts

        vectors, _emb = embed_texts([query], kind="query")
        qvec = vectors[0]
    except Exception as exc:
        report["detail"] = f"Embeddings unavailable — semantic store skipped ({exc})"
        return report

    entry_id = prompt_cache_id(query, model, extra) + "s"
    entry = {
        "id": entry_id,
        "query": query,
        "model": model,
        "vector": qvec,
        "cached_at": _now(),
    }
    _sem_local[entry_id] = entry
    report.update(wrote=True, store="in_memory", detail="Stored query embedding in local semantic index")
    logger.info("Semantic cache STORE %s (local index size %d)", entry_id, len(_sem_local))
    client = get_redis()
    if client is None:
        return report
    try:
        client.hset(SEMANTIC_INDEX_KEY, entry_id, json.dumps(entry))
        client.expire(SEMANTIC_INDEX_KEY, PROMPT_CACHE_TTL * 2)
        # Trim oldest entries beyond the cap
        if client.hlen(SEMANTIC_INDEX_KEY) > SEMANTIC_MAX_ENTRIES:
            all_entries = {k: json.loads(v) for k, v in client.hgetall(SEMANTIC_INDEX_KEY).items()}
            for old_id in sorted(all_entries, key=lambda k: all_entries[k].get("cached_at", ""))[: len(all_entries) - SEMANTIC_MAX_ENTRIES]:
                client.hdel(SEMANTIC_INDEX_KEY, old_id)
                _sem_local.pop(old_id, None)
        report.update(store="redis", detail=f"Stored query embedding in Redis semantic index (threshold {SEMANTIC_CACHE_THRESHOLD:.2f})")
    except Exception as exc:
        report["detail"] = f"Local semantic store ok; Redis failed: {exc}"
    return report
