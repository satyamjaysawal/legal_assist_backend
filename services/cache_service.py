import hashlib
import json
import math
import os
import re
from datetime import datetime, timezone
from typing import Any

import logging

from services.memory_service import get_redis

logger = logging.getLogger("legal_assist.cache")

PROMPT_CACHE_TTL = int(os.getenv("PROMPT_CACHE_TTL", "21600"))
# Master switch for the semantic (embedding-similarity) cache layer.
# Disabled by default: identity-style questions must reach the memory
# layer, and exact-match caching already covers repeated prompts.
SEMANTIC_CACHE_ENABLED = os.getenv("SEMANTIC_CACHE_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")
SEMANTIC_CACHE_THRESHOLD = float(os.getenv("SEMANTIC_CACHE_THRESHOLD", "0.55"))
# Queries that reduce to fewer content tokens than this are too generic
# to cache semantically (e.g. "who am I", "help") — they would match
# unrelated queries and serve wrong cached answers.
SEMANTIC_MIN_TOKENS = int(os.getenv("SEMANTIC_MIN_TOKENS", "2"))
SEMANTIC_MAX_ENTRIES = int(os.getenv("SEMANTIC_MAX_ENTRIES", "300"))
_local: dict[str, dict[str, Any]] = {}
_sem_local: dict[str, dict[str, Any]] = {}

# ── Privacy invariant ────────────────────────────────────────────
# Every cache layer is strictly PER-USER. Replies are personalized from
# the caller's memory (profile PII, facts, episodes), so a cache entry
# written by user A must never be readable by user B. user_id is part of
# every key/hash; there is no global (user-less) cache or memory store.
# Entries written before this rule carried no user_id and are simply
# unreachable now — they expire on their own TTL.


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_prompt(query: str) -> str:
    return re.sub(r"\s+", " ", (query or "").strip().lower())


# Personal / identity questions depend on per-user memory (profile facts,
# prior turns), so they must NEVER be served from or stored in the shared
# prompt cache — a cached answer would ignore what the bot knows about
# this specific user.
_PERSONAL_PATTERNS = [
    r"\bwho\s+(?:am\s+i|i\s+am|i'?m\s+i)\b",
    r"\bwhat(?:'s|\s+is)?\s+my\s+name\b",
    r"\bdo\s+you\s+(?:know|remember)\s+me\b",
    r"\b(?:tell\s+me\s+)?about\s+me\b",
    r"\bmy\s+identity\b",
    r"\bremember\s+(?:about\s+)?me\b",
]


def is_personal_query(query: str) -> bool:
    norm = normalize_prompt(query)
    return any(re.search(p, norm) for p in _PERSONAL_PATTERNS)


def prompt_cache_id(query: str, model: str, extra: str = "", user_id: str = "") -> str:
    raw = f"{user_id or 'anon'}|{model}|{normalize_prompt(query)}|{extra or 'none'}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def prompt_cache_key(query: str, model: str, extra: str = "", user_id: str = "") -> str:
    return f"legal_assist:pcache:{user_id or 'anon'}:{prompt_cache_id(query, model, extra, user_id)}"


def cache_status() -> dict[str, Any]:
    client = get_redis()
    return {
        "ok": client is not None,
        "store": "redis+memory",
        "ttl_seconds": PROMPT_CACHE_TTL,
        "local_entries": len(_local),
    }


def get_prompt_cache(query: str, model: str, extra: str = "", user_id: str = "") -> tuple[dict[str, Any] | None, dict[str, Any]]:
    cache_id = prompt_cache_id(query, model, extra, user_id)
    key = prompt_cache_key(query, model, extra, user_id)
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
    if is_personal_query(query):
        report["detail"] = "Personal query — cache bypassed so memory can answer"
        logger.info("Exact cache BYPASS (personal query): %r", query[:80])
        return None, report
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


def set_prompt_cache(query: str, model: str, payload: dict[str, Any], extra: str = "", user_id: str = "") -> dict[str, Any]:
    if is_personal_query(query):
        return {
            "name": "prompt_cache",
            "label": "Prompt cache",
            "wrote": False,
            "store": None,
            "when": _now(),
            "detail": "Personal query — never cached (memory-dependent answer)",
        }
    key = prompt_cache_key(query, model, extra, user_id)
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


def _sem_index_key(user_id: str) -> str:
    return f"legal_assist:scache:index:{user_id or 'anon'}"


def _load_sem_index(user_id: str) -> dict[str, dict[str, Any]]:
    """Merge the local semantic index with the Redis-backed one (per user)."""
    merged = {k: v for k, v in _sem_local.items() if (v.get("user_id") or "anon") == (user_id or "anon")}
    client = get_redis()
    if client is None:
        return merged
    try:
        for entry_id, raw in (client.hgetall(_sem_index_key(user_id)) or {}).items():
            try:
                merged[entry_id] = json.loads(raw)
            except Exception:
                continue
    except Exception:
        pass
    return merged


def semantic_cache_lookup(query: str, model: str, extra: str = "", user_id: str = "") -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Find a cached answer whose query embedding covers this query.

    Uses containment scoring — how much of THIS query's content is
    covered by the cached entry — so a short paraphrase of a longer
    cached query still hits, while unrelated topics stay near zero.
    """
    report: dict[str, Any] = {
        "name": "semantic_cache",
        "label": "Semantic cache",
        "used": False,
        "status": "miss",
        "threshold": SEMANTIC_CACHE_THRESHOLD,
        "when": _now(),
        "detail": "No semantically similar cached query",
    }
    if not SEMANTIC_CACHE_ENABLED:
        report.update(status="skip", detail="Semantic cache disabled (SEMANTIC_CACHE_ENABLED=false)")
        return None, report
    index = _load_sem_index(user_id)
    if not index:
        report["detail"] = "Semantic index empty — first query stores it"
        return None, report
    try:
        from services.embedding_service import embed_texts

        vectors, _emb = embed_texts([query], kind="query")
        qvec = vectors[0]
        q_norm = (_emb.get("norms") or [1.0])[0]
    except Exception as exc:
        report.update(status="skip", detail=f"Embeddings unavailable — semantic cache skipped ({exc})")
        return None, report
    if not q_norm:
        report["detail"] = "Query has no comparable content tokens — semantic cache skipped"
        return None, report

    # Symmetric guard with the store: ultra-generic queries must not
    # match cached entries (they would get unrelated cached answers).
    try:
        from services.embedding_service import _tokenize

        if len(_tokenize(query)) < SEMANTIC_MIN_TOKENS:
            report["detail"] = "Query too generic for semantic matching"
            return None, report
    except Exception:
        pass

    best_id, best_score, best_entry = "", 0.0, None
    for entry_id, entry in index.items():
        if entry.get("model") != model:
            continue
        # Ignore stale/too-generic entries stored before the min-token guard
        if (entry.get("tokens") or 0) < SEMANTIC_MIN_TOKENS:
            continue
        dvec = entry.get("vector") or []
        if not dvec:
            continue
        # Containment scoring: cosine scaled by cached/query norm ratio so a
        # short paraphrase of a longer cached query still hits. Capped so a
        # tiny query can never blow a small overlap past the threshold.
        d_norm = entry.get("norm") or 1.0
        ratio = min(1.6, max(0.5, d_norm / q_norm)) if q_norm else 1.0
        score = min(1.0, _cosine(qvec, dvec) * ratio)
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

    key = prompt_cache_key(best_entry.get("query") or query, model, extra, user_id)
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
        detail=f"{best_score:.0%} match with cached query “{(best_entry.get('query') or '')[:60]}”",
    )
    return payload, report


def semantic_cache_store(query: str, model: str, extra: str = "", user_id: str = "") -> dict[str, Any]:
    """Embed this query and add it to the semantic cache index."""
    report: dict[str, Any] = {
        "name": "semantic_cache",
        "label": "Semantic cache",
        "wrote": False,
        "when": _now(),
        "detail": "",
    }
    if not SEMANTIC_CACHE_ENABLED:
        report["detail"] = "Semantic cache disabled (SEMANTIC_CACHE_ENABLED=false)"
        return report
    try:
        from services.embedding_service import embed_texts

        vectors, _emb = embed_texts([query], kind="query")
        qvec = vectors[0]
        q_norm = (_emb.get("norms") or [1.0])[0]
    except Exception as exc:
        report["detail"] = f"Embeddings unavailable — semantic store skipped ({exc})"
        return report
    if not q_norm:
        report["detail"] = "Query has no comparable content tokens — semantic store skipped"
        return report

    # Guard: don't pollute the index with ultra-generic queries.
    try:
        from services.embedding_service import _tokenize

        n_tokens = len(_tokenize(query))
    except Exception:
        n_tokens = SEMANTIC_MIN_TOKENS
    if n_tokens < SEMANTIC_MIN_TOKENS:
        report["detail"] = f"Query too generic to cache semantically ({n_tokens} content token(s))"
        return report

    entry_id = prompt_cache_id(query, model, extra, user_id) + "s"
    entry = {
        "id": entry_id,
        "user_id": user_id or "anon",
        "query": query,
        "model": model,
        "vector": qvec,
        "norm": q_norm,
        "tokens": n_tokens,
        "cached_at": _now(),
    }
    _sem_local[entry_id] = entry
    report.update(wrote=True, store="in_memory", detail="Stored query embedding in local semantic index")
    logger.info("Semantic cache STORE %s (local index size %d)", entry_id, len(_sem_local))
    client = get_redis()
    if client is None:
        return report
    try:
        index_key = _sem_index_key(user_id)
        client.hset(index_key, entry_id, json.dumps(entry))
        client.expire(index_key, PROMPT_CACHE_TTL * 2)
        # Trim oldest entries beyond the cap
        if client.hlen(index_key) > SEMANTIC_MAX_ENTRIES:
            all_entries = {k: json.loads(v) for k, v in client.hgetall(index_key).items()}
            for old_id in sorted(all_entries, key=lambda k: all_entries[k].get("cached_at", ""))[: len(all_entries) - SEMANTIC_MAX_ENTRIES]:
                client.hdel(index_key, old_id)
                _sem_local.pop(old_id, None)
        report.update(store="redis", detail=f"Stored query embedding in Redis semantic index (threshold {SEMANTIC_CACHE_THRESHOLD:.2f})")
    except Exception as exc:
        report["detail"] = f"Local semantic store ok; Redis failed: {exc}"
    return report
