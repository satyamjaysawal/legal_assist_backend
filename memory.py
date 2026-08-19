import json
import os
import re
from datetime import datetime, timezone
from typing import Any

import logging

logger = logging.getLogger("legal_assist.memory")

STM_TTL_SECONDS = int(os.getenv("REDIS_TTL_SECONDS", "21600"))
STM_WINDOW = 20
LTM_LIMIT = 8
MONGO_DB = os.getenv("MONGODB_DB", "legal_assist_inhouse")
LTM_COLLECTION = "long_term_memory"
PROFILE_COLLECTION = "user_profiles"
SEMANTIC_COLLECTION = "semantic_memory"
EPISODIC_COLLECTION = "episodic_memory"
PROCEDURAL_COLLECTION = "procedural_memory"

_in_memory: dict[str, list[dict[str, str]]] = {}
_redis = None
_mongo = None
_redis_error = ""
_mongo_error = ""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_redis():
    global _redis, _redis_error
    if _redis is not None:
        return _redis
    host = os.getenv("REDIS_HOST")
    if not host:
        _redis_error = "REDIS_HOST is not set"
        return None
    try:
        import redis

        kwargs = {
            "host": host,
            "port": int(os.getenv("REDIS_PORT", "6379")),
            "password": os.getenv("REDIS_PASSWORD") or None,
            "username": os.getenv("REDIS_USERNAME") or None,
            "decode_responses": True,
            "socket_connect_timeout": 5,
            "socket_timeout": 5,
        }
        client = redis.Redis(**kwargs)
        try:
            client.ping()
        except Exception:
            client = redis.Redis(**kwargs, ssl=True)
            client.ping()
        _redis = client
        _redis_error = ""
        return _redis
    except Exception as exc:
        _redis_error = str(exc)
        return None


def get_mongo():
    global _mongo, _mongo_error
    if _mongo is not None:
        return _mongo
    uri = os.getenv("MONGODB_URI")
    if not uri:
        _mongo_error = "MONGODB_URI is not set"
        return None
    try:
        from pymongo import MongoClient

        client = MongoClient(uri, serverSelectionTimeoutMS=6000)
        client.admin.command("ping")
        _mongo = client
        _mongo_error = ""
        return _mongo
    except Exception as exc:
        _mongo_error = str(exc)
        return None


def stm_key(user_id: str, journey_id: str) -> str:
    return f"legal_assist:stm:{user_id}:{journey_id}"


def cache_key(user_id: str, journey_id: str) -> str:
    return f"{user_id}:{journey_id}"


def layer_status() -> dict[str, Any]:
    redis_ok = False
    mongo_ok = False
    get_redis()
    get_mongo()
    if _redis is not None:
        try:
            redis_ok = bool(_redis.ping())
        except Exception as exc:
            redis_ok = False
            globals()["_redis_error"] = str(exc)
    if _mongo is not None:
        try:
            _mongo.admin.command("ping")
            mongo_ok = True
        except Exception as exc:
            mongo_ok = False
            globals()["_mongo_error"] = str(exc)
    return {
        "in_memory": {"ok": True, "store": "process", "sessions": len(_in_memory)},
        "short_term": {
            "ok": redis_ok,
            "store": "redis",
            "host": os.getenv("REDIS_HOST") or "",
            "error": _redis_error,
        },
        "long_term": {
            "ok": mongo_ok,
            "store": "mongodb",
            "db": MONGO_DB,
            "collection": LTM_COLLECTION,
            "error": _mongo_error,
        },
        "prompt_cache": {
            "ok": redis_ok,
            "store": "redis+memory",
            "ttl_seconds": int(os.getenv("PROMPT_CACHE_TTL", "21600")),
        },
        "qdrant": {
            "ok": bool(os.getenv("QUDRANT_CLUSTER_ENDPOINT") and os.getenv("QUDRANT_VECTOR_DB_API_KEY")),
            "store": "qdrant",
            "host": os.getenv("QUDRANT_CLUSTER_ENDPOINT") or "",
            "collection": os.getenv("QDRANT_COLLECTION", "legal_assist_docs"),
        },
    }


def load_in_memory(user_id: str, journey_id: str) -> tuple[list[dict[str, str]], dict[str, Any]]:
    turns = _in_memory.get(cache_key(user_id, journey_id)) or []
    return turns, {
        "name": "in_memory",
        "label": "In-memory",
        "store": "process RAM",
        "used": bool(turns),
        "status": "hit" if turns else "miss",
        "when": _now(),
        "turns": len(turns),
        "detail": f"{len(turns)} cached turn(s) for this journey" if turns else "No process cache for this journey",
    }


def load_short_term(user_id: str, journey_id: str) -> tuple[list[dict[str, str]], dict[str, Any]]:
    client = get_redis()
    report = {
        "name": "short_term",
        "label": "Short-term",
        "store": "Redis",
        "used": False,
        "status": "miss",
        "when": _now(),
        "turns": 0,
        "ttl_seconds": None,
        "key": stm_key(user_id, journey_id),
        "detail": "Redis miss",
    }
    if client is None:
        report["status"] = "error"
        report["detail"] = _redis_error or "Redis unavailable"
        return [], report
    try:
        raw = client.get(stm_key(user_id, journey_id))
        ttl = client.ttl(stm_key(user_id, journey_id))
        report["ttl_seconds"] = ttl if isinstance(ttl, int) and ttl >= 0 else None
        if not raw:
            report["detail"] = "No Redis session window"
            return [], report
        turns = json.loads(raw)
        if not isinstance(turns, list):
            turns = []
        report["used"] = bool(turns)
        report["status"] = "hit" if turns else "miss"
        report["turns"] = len(turns)
        report["detail"] = f"{len(turns)} message(s) in Redis window"
        return turns, report
    except Exception as exc:
        report["status"] = "error"
        report["detail"] = str(exc)
        return [], report


def load_long_term(user_id: str, query: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    report = {
        "name": "long_term",
        "label": "Long-term",
        "store": "MongoDB",
        "used": False,
        "status": "miss",
        "when": _now(),
        "hits": 0,
        "db": MONGO_DB,
        "collection": LTM_COLLECTION,
        "facts": [],
        "detail": "No long-term facts",
    }
    if not user_id:
        report["status"] = "skip"
        report["detail"] = "No user_id"
        return [], report
    client = get_mongo()
    if client is None:
        report["status"] = "error"
        report["detail"] = _mongo_error or "MongoDB unavailable"
        return [], report
    try:
        col = client[MONGO_DB][LTM_COLLECTION]
        docs = list(
            col.find({"user_id": user_id}, {"_id": 0}).sort("created_at", -1).limit(40)
        )
        words = [w.lower() for w in re.findall(r"[a-zA-Z]{3,}", query or "")]
        scored: list[dict[str, Any]] = []
        for doc in docs:
            blob = " ".join(
                str(doc.get(k) or "") for k in ("query", "summary", "reply_excerpt", "domain")
            ).lower()
            score = sum(1 for w in words if w in blob) if words else 0
            scored.append({**doc, "_score": score})
        scored.sort(key=lambda item: (item["_score"], item.get("created_at") or ""), reverse=True)
        picked = scored[:LTM_LIMIT]
        facts = [
            {
                "summary": item.get("summary") or item.get("query") or "",
                "domain": item.get("domain") or "general",
                "created_at": item.get("created_at") or "",
            }
            for item in picked
            if (item.get("summary") or item.get("query"))
        ]
        report["used"] = bool(facts)
        report["status"] = "hit" if facts else "miss"
        report["hits"] = len(facts)
        report["facts"] = facts
        report["detail"] = f"{len(facts)} fact(s) from {MONGO_DB}.{LTM_COLLECTION}"
        return facts, report
    except Exception as exc:
        report["status"] = "error"
        report["detail"] = str(exc)
        return [], report


def list_user_facts(user_id: str, limit: int = 40) -> list[dict[str, Any]]:
    client = get_mongo()
    if client is None or not user_id:
        return []
    docs = list(
        client[MONGO_DB][LTM_COLLECTION]
        .find({"user_id": user_id}, {"_id": 0})
        .sort("created_at", -1)
        .limit(limit)
    )
    return [
        {
            "summary": item.get("summary") or item.get("query") or "",
            "query": item.get("query") or "",
            "domain": item.get("domain") or "general",
            "intent": item.get("intent") or "",
            "journey_id": item.get("journey_id") or item.get("session_id") or "",
            "reply_excerpt": item.get("reply_excerpt") or "",
            "created_at": item.get("created_at") or "",
        }
        for item in docs
        if item.get("summary") or item.get("query")
    ]


def merge_history(*histories: list[dict[str, str]]) -> list[dict[str, str]]:
    longest: list[dict[str, str]] = []
    for hist in histories:
        cleaned = [
            {"role": m.get("role", "user"), "content": (m.get("content") or "").strip()}
            for m in hist
            if (m.get("content") or "").strip() and m.get("role") in {"user", "assistant"}
        ]
        if len(cleaned) > len(longest):
            longest = cleaned
    return longest


def load_thread(user_id: str, journey_id: str) -> tuple[list[dict[str, str]], dict[str, Any]]:
    report = {
        "name": "thread",
        "label": "User thread",
        "store": "MongoDB journeys",
        "used": False,
        "status": "miss",
        "when": _now(),
        "turns": 0,
        "journey_id": journey_id,
        "detail": "No saved thread",
    }
    try:
        from journeys import load_journey_messages

        turns = load_journey_messages(user_id, journey_id)
        report["used"] = bool(turns)
        report["status"] = "hit" if turns else "miss"
        report["turns"] = len(turns)
        report["detail"] = f"{len(turns)} message(s) in journey {journey_id[:8]}"
        return turns, report
    except Exception as exc:
        report["status"] = "error"
        report["detail"] = str(exc)
        return [], report


def load_all(
    user_id: str,
    journey_id: str,
    incoming: list[dict[str, str]],
    query: str,
):
    inmem, inmem_report = load_in_memory(user_id, journey_id)
    stm, stm_report = load_short_term(user_id, journey_id)
    thread, thread_report = load_thread(user_id, journey_id)
    facts, ltm_report = load_long_term(user_id, query)
    history = merge_history(inmem, stm, thread, incoming)

    # Semantic memory (vector similarity)
    semantic_facts, semantic_report = load_semantic_facts(user_id, query)

    # Episodic memory (past conversation episodes)
    recent_eps = load_recent_episodes(user_id, limit=3)
    relevant_eps = load_relevant_episodes(user_id, query, limit=3)
    # Deduplicate by journey_id
    seen_jids = set()
    episodes = []
    for ep in recent_eps + relevant_eps:
        jid = ep.get("journey_id")
        if jid not in seen_jids:
            seen_jids.add(jid)
            episodes.append(ep)
    episodic_notes = format_episodes(episodes[:5])
    episodic_report = {
        "name": "episodic",
        "label": "Episodic",
        "store": "MongoDB",
        "used": bool(episodes),
        "status": "hit" if episodes else "miss",
        "when": _now(),
        "episodes": len(episodes),
        "detail": f"{len(episodes)} episode(s) recalled",
    }

    # Procedural memory (user preferences)
    procedural_notes, proc_report = load_procedural_memory(user_id)

    notes = ""
    if facts:
        notes = "\n".join(
            f"- ({item['domain']}) {item['summary']}"
            for item in facts
            if item.get("summary")
        )
    # Merge semantic facts into notes
    if semantic_facts:
        semantic_lines = "\n".join(
            f"- [{f.get('domain', 'general')}] {f['text']}"
            for f in semantic_facts
            if f.get("text")
        )
        notes = (notes + "\n" + semantic_lines).strip() if notes else semantic_lines

    return {
        "history": history,
        "facts": facts,
        "notes": notes,
        "episodic_notes": episodic_notes,
        "procedural_notes": procedural_notes,
        "layers": [inmem_report, stm_report, thread_report, ltm_report, semantic_report, episodic_report, proc_report],
    }


def clear_journey_memory(user_id: str, journey_id: str) -> dict[str, Any]:
    key = cache_key(user_id, journey_id)
    _in_memory.pop(key, None)
    redis_ok = False
    mongo_ok = False
    client = get_redis()
    if client is not None:
        try:
            client.delete(stm_key(user_id, journey_id))
            redis_ok = True
        except Exception:
            redis_ok = False
    mongo = get_mongo()
    if mongo is not None:
        try:
            mongo[MONGO_DB][LTM_COLLECTION].delete_many(
                {"user_id": user_id, "$or": [{"journey_id": journey_id}, {"session_id": journey_id}]}
            )
            mongo_ok = True
        except Exception:
            mongo_ok = False
    return {"in_memory": True, "short_term": redis_ok, "long_term": mongo_ok}


def save_in_memory(user_id: str, journey_id: str, messages: list[dict[str, str]]) -> dict[str, Any]:
    key = cache_key(user_id, journey_id)
    _in_memory[key] = messages[-STM_WINDOW:]
    return {
        "name": "in_memory",
        "label": "In-memory",
        "store": "process RAM",
        "wrote": True,
        "when": _now(),
        "turns": len(_in_memory[key]),
        "detail": "Wrote working set to process cache",
    }


def save_short_term(user_id: str, journey_id: str, messages: list[dict[str, str]]) -> dict[str, Any]:
    report = {
        "name": "short_term",
        "label": "Short-term",
        "store": "Redis",
        "wrote": False,
        "when": _now(),
        "ttl_seconds": STM_TTL_SECONDS,
        "detail": "Redis write skipped",
    }
    client = get_redis()
    if client is None:
        report["detail"] = _redis_error or "Redis unavailable"
        return report
    try:
        window = messages[-STM_WINDOW:]
        client.setex(stm_key(user_id, journey_id), STM_TTL_SECONDS, json.dumps(window))
        report["wrote"] = True
        report["turns"] = len(window)
        report["detail"] = f"Saved {len(window)} message(s), TTL {STM_TTL_SECONDS}s"
        return report
    except Exception as exc:
        report["detail"] = str(exc)
        return report


def save_long_term(
    user_id: str,
    session_id: str,
    query: str,
    reply: str,
    analysis: dict[str, Any] | None,
) -> dict[str, Any]:
    report = {
        "name": "long_term",
        "label": "Long-term",
        "store": "MongoDB",
        "wrote": False,
        "when": _now(),
        "db": MONGO_DB,
        "detail": "Mongo write skipped",
    }
    if not user_id:
        report["detail"] = "No user_id"
        return report
    client = get_mongo()
    if client is None:
        report["detail"] = _mongo_error or "MongoDB unavailable"
        return report
    try:
        analysis = analysis or {}
        doc = {
            "user_id": user_id,
            "journey_id": session_id,
            "session_id": session_id,
            "kind": "turn",
            "query": query[:500],
            "summary": (analysis.get("summary") or query)[:400],
            "domain": analysis.get("domain") or "general",
            "intent": analysis.get("intent") or "question",
            "reply_excerpt": reply[:500],
            "created_at": _now(),
        }
        client[MONGO_DB][LTM_COLLECTION].insert_one(doc)
        report["wrote"] = True
        report["detail"] = f"Saved fact to {MONGO_DB}.{LTM_COLLECTION}"
        return report
    except Exception as exc:
        report["detail"] = str(exc)
        return report


def save_thread(user_id: str, journey_id: str, messages: list[dict[str, str]], query: str):
    report = {
        "name": "thread",
        "label": "User thread",
        "store": "MongoDB journeys",
        "wrote": False,
        "when": _now(),
        "journey_id": journey_id,
        "detail": "Thread write skipped",
    }
    try:
        from journeys import save_journey_thread

        saved = save_journey_thread(user_id, journey_id, messages, title_hint=query)
        report["wrote"] = bool(saved.get("wrote"))
        report["turns"] = saved.get("turns", 0)
        report["detail"] = f"Saved thread {journey_id[:8]} ({saved.get('turns', 0)} msgs)"
        return report
    except Exception as exc:
        report["detail"] = str(exc)
        return report


def save_all(
    journey_id: str,
    user_id: str,
    messages: list[dict[str, str]],
    query: str,
    reply: str,
    analysis: dict[str, Any] | None,
    user_text: str = "",
):
    # `query` may be a generated chat title (used for episode/thread labels);
    # profile facts must be mined from the user's actual message instead.
    raw_user_text = user_text or query
    writes = [
        save_in_memory(user_id, journey_id, messages),
        save_short_term(user_id, journey_id, messages),
        save_thread(user_id, journey_id, messages, query),
        save_long_term(user_id, journey_id, query, reply, analysis),
    ]
    # Semantic memory — embed and store the turn
    try:
        sem = save_semantic_fact(user_id, query, (analysis or {}).get("domain", "general"), journey_id)
        if sem:
            writes.append(sem)
    except Exception as exc:
        logger.warning("Semantic memory save failed: %s", exc)
    # Episodic memory — save episode
    try:
        ep = save_episode(user_id, journey_id, messages, query, reply, analysis)
        if ep:
            writes.append(ep)
    except Exception as exc:
        logger.warning("Episodic memory save failed: %s", exc)
    # Procedural memory — detect preferences
    try:
        proc = update_procedural_memory(user_id, messages, query)
        if proc:
            writes.append(proc)
    except Exception as exc:
        logger.warning("Procedural memory save failed: %s", exc)
    # Profile extraction
    try:
        profile_write = extract_and_save_profile(user_id, raw_user_text)
        if profile_write:
            writes.append(profile_write)
    except Exception as exc:
        logger.warning("Profile extraction failed: %s", exc)
    return writes


# ── User Profile System ──────────────────────────────────────────

# Patterns to extract personal information from user messages.
# Note: `i'(?=\s)` catches "I' rahul" (apostrophe + space) but not "i'll";
# the lookahead after "this is" avoids capturing filler phrases.
_NAME_PATTERNS = [
    re.compile(
        r"(?:my name is|i'm|i am|i'(?=\s)|call me"
        r"|this is(?!\s+(?:not|a|an|the|my|your|his|her|our|their|why|how|what|when|where|who|just|still|very)\b))"
        r"\s*([A-Za-z][a-z]+(?:\s+[A-Za-z][a-z]+)?)",
        re.IGNORECASE,
    ),
    re.compile(r"(?:name[:\s]+)\s*([A-Za-z][a-z]+(?:\s+[A-Za-z][a-z]+)?)", re.IGNORECASE),
]
# Words that look like name matches but are ordinary replies.
_NOT_NAMES = {
    "fine", "good", "okay", "ok", "here", "there", "not", "sure", "happy",
    "ready", "back", "done", "sorry", "confused", "stuck", "new", "alone",
}
_EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
_PHONE_PATTERN = re.compile(r"(?:\+?\d{1,3}[\s\-]?)?\(?\d{2,5}\)?[\s\-]?\d{3,5}[\s\-]?\d{3,5}")
_FACT_PATTERNS = [
    re.compile(r"i (?:live in|am from|reside in)\s+(.+?)(?:\.|$)", re.IGNORECASE),
    re.compile(r"i (?:work as|am a|am an)\s+(.+?)(?:\.|$)", re.IGNORECASE),
    re.compile(r"my (?:company|firm|organisation) is\s+(.+?)(?:\.|$)", re.IGNORECASE),
]


def extract_and_save_profile(user_id: str, text: str) -> dict[str, Any] | None:
    """Extract personal information from user text and update profile."""
    if not user_id or not text:
        return None
    updates: dict[str, Any] = {}

    # Extract name
    for pat in _NAME_PATTERNS:
        m = pat.search(text)
        if m:
            name = m.group(1).strip()
            if 2 <= len(name) <= 60 and name.lower() not in _NOT_NAMES:
                updates["name"] = name
            break

    # Extract email
    m = _EMAIL_PATTERN.search(text)
    if m:
        updates["email"] = m.group(0)

    # Extract phone
    m = _PHONE_PATTERN.search(text)
    if m:
        phone = m.group(0).strip()
        if len(phone) >= 8:
            updates["phone"] = phone

    # Extract facts (location, profession, etc.)
    for pat in _FACT_PATTERNS:
        m = pat.search(text)
        if m:
            fact = m.group(0).strip().lower()
            if "facts" not in updates:
                updates["facts"] = []
            updates["facts"].append(fact)

    if not updates:
        return None

    client = get_mongo()
    if client is None:
        return None

    try:
        col = client[MONGO_DB][PROFILE_COLLECTION]
        existing = col.find_one({"user_id": user_id}, {"_id": 0}) or {}

        # Merge updates into existing profile
        for key, value in updates.items():
            if key == "facts":
                old_facts = set(existing.get("facts") or [])
                old_facts.update(value)
                existing["facts"] = list(old_facts)[-20:]  # keep last 20
            else:
                existing[key] = value

        existing["user_id"] = user_id
        existing["updated_at"] = _now()
        if "created_at" not in existing:
            existing["created_at"] = _now()

        col.update_one(
            {"user_id": user_id},
            {"$set": existing},
            upsert=True,
        )
        return {
            "name": "profile",
            "label": "User profile",
            "store": "MongoDB",
            "wrote": True,
            "when": _now(),
            "detail": f"Updated profile: {list(updates.keys())}",
        }
    except Exception as exc:
        return {
            "name": "profile",
            "label": "User profile",
            "store": "MongoDB",
            "wrote": False,
            "detail": str(exc),
        }


def load_user_profile(user_id: str) -> tuple[str, dict[str, Any]]:
    """Load user profile and return (formatted_text, raw_profile)."""
    report = {
        "name": "profile",
        "label": "User profile",
        "store": "MongoDB",
        "used": False,
        "status": "miss",
        "when": _now(),
        "detail": "No profile",
    }
    if not user_id:
        return "", report
    client = get_mongo()
    if client is None:
        report["status"] = "error"
        report["detail"] = _mongo_error or "MongoDB unavailable"
        return "", report
    try:
        col = client[MONGO_DB][PROFILE_COLLECTION]
        profile = col.find_one({"user_id": user_id}, {"_id": 0})
        if not profile:
            return "", report

        parts = []
        if profile.get("name"):
            parts.append(f"User's name: {profile['name']}")
        if profile.get("email"):
            parts.append(f"Email: {profile['email']}")
        if profile.get("phone"):
            parts.append(f"Phone: {profile['phone']}")
        for fact in (profile.get("facts") or []):
            parts.append(fact.capitalize())

        text = "\n".join(parts) if parts else ""
        report["used"] = bool(text)
        report["status"] = "hit" if text else "miss"
        report["detail"] = f"Profile: {profile.get('name', 'unknown')}" if text else "Empty profile"
        return text, report
    except Exception as exc:
        report["status"] = "error"
        report["detail"] = str(exc)
        return "", report


def get_user_profile(user_id: str) -> dict[str, Any] | None:
    """Get raw user profile document."""
    if not user_id:
        return None
    client = get_mongo()
    if client is None:
        return None
    try:
        return client[MONGO_DB][PROFILE_COLLECTION].find_one(
            {"user_id": user_id}, {"_id": 0}
        )
    except Exception:
        return None


def update_user_profile(user_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    """Manually update user profile fields."""
    if not user_id:
        return {"ok": False, "detail": "No user_id"}
    client = get_mongo()
    if client is None:
        return {"ok": False, "detail": "MongoDB unavailable"}
    try:
        col = client[MONGO_DB][PROFILE_COLLECTION]
        updates["updated_at"] = _now()
        col.update_one(
            {"user_id": user_id},
            {"$set": updates},
            upsert=True,
        )
        return {"ok": True, "updated": list(updates.keys())}
    except Exception as exc:
        return {"ok": False, "detail": str(exc)}


# ═════════════════════════════════════════════════════════════════
# SEMANTIC MEMORY — vector-based fact retrieval
# ═════════════════════════════════════════════════════════════════

def _get_semantic_qdrant():
    """Get Qdrant client and ensure semantic collection exists."""
    try:
        from qdrant_client import QdrantClient
        from qdrant_client.models import Distance, VectorParams, PayloadSchemaType
    except ImportError:
        return None, "qdrant_client not installed"
    url = os.getenv("QUDRANT_CLUSTER_ENDPOINT") or os.getenv("QDRANT_URL") or ""
    api_key = os.getenv("QUDRANT_VECTOR_DB_API_KEY") or os.getenv("QDRANT_API_KEY") or ""
    if not url or not api_key:
        return None, "Qdrant not configured"
    try:
        client = QdrantClient(url=url, api_key=api_key, timeout=15)
        if not client.collection_exists(SEMANTIC_COLLECTION):
            client.create_collection(
                collection_name=SEMANTIC_COLLECTION,
                vectors_config=VectorParams(size=int(os.getenv("EMBED_DIM", "768")), distance=Distance.COSINE),
            )
            for field in ("user_id", "domain", "journey_id"):
                try:
                    client.create_payload_index(
                        collection_name=SEMANTIC_COLLECTION,
                        field_name=field,
                        field_schema=PayloadSchemaType.KEYWORD,
                    )
                except Exception:
                    pass
        return client, ""
    except Exception as exc:
        return None, str(exc)


def save_semantic_fact(user_id: str, text: str, domain: str, journey_id: str) -> dict[str, Any] | None:
    """Embed and store a fact in semantic memory (Qdrant)."""
    if not user_id or not text or len(text.strip()) < 5:
        return None
    try:
        from embeddings import embed_texts
        vectors, _ = embed_texts([text], kind="document")
    except Exception as exc:
        return {"name": "semantic", "label": "Semantic", "store": "qdrant", "wrote": False, "detail": f"Embed failed: {exc}"}
    client, err = _get_semantic_qdrant()
    if client is None:
        return {"name": "semantic", "label": "Semantic", "store": "qdrant", "wrote": False, "detail": err}
    try:
        from qdrant_client.models import PointStruct
        import uuid as _uuid
        point = PointStruct(
            id=str(_uuid.uuid4()),
            vector=vectors[0],
            payload={
                "user_id": user_id,
                "text": text[:500],
                "domain": domain or "general",
                "journey_id": journey_id,
                "created_at": _now(),
            },
        )
        client.upsert(collection_name=SEMANTIC_COLLECTION, points=[point], wait=True)
        return {
            "name": "semantic",
            "label": "Semantic",
            "store": "qdrant",
            "wrote": True,
            "when": _now(),
            "detail": f"Saved semantic fact ({domain})",
        }
    except Exception as exc:
        return {"name": "semantic", "label": "Semantic", "store": "qdrant", "wrote": False, "detail": str(exc)}


def load_semantic_facts(user_id: str, query: str, limit: int = 5) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Retrieve semantically similar facts from Qdrant."""
    report = {
        "name": "semantic",
        "label": "Semantic",
        "store": "qdrant",
        "used": False,
        "status": "miss",
        "when": _now(),
        "hits": 0,
        "detail": "No semantic facts",
    }
    if not user_id or not query or len(query.strip()) < 3:
        return [], report
    client, err = _get_semantic_qdrant()
    if client is None:
        report["status"] = "error"
        report["detail"] = err
        return [], report
    try:
        from embeddings import embed_texts
        from qdrant_client.models import FieldCondition, Filter, MatchValue
        vectors, _ = embed_texts([query], kind="query")
        qfilter = Filter(must=[FieldCondition(key="user_id", match=MatchValue(value=user_id))])
        result = client.query_points(
            collection_name=SEMANTIC_COLLECTION,
            query=vectors[0],
            query_filter=qfilter,
            limit=limit,
            with_payload=True,
        )
        facts = []
        for point in result.points:
            payload = point.payload or {}
            text = str(payload.get("text") or "").strip()
            if text:
                facts.append({
                    "text": text,
                    "domain": payload.get("domain", "general"),
                    "score": round(float(point.score or 0), 4),
                    "journey_id": payload.get("journey_id", ""),
                })
        report["used"] = bool(facts)
        report["status"] = "hit" if facts else "miss"
        report["hits"] = len(facts)
        report["detail"] = f"{len(facts)} semantic fact(s) via vector similarity"
        return facts, report
    except Exception as exc:
        report["status"] = "error"
        report["detail"] = str(exc)
        return [], report


# ═════════════════════════════════════════════════════════════════
# EPISODIC MEMORY — conversation episodes with temporal context
# ═════════════════════════════════════════════════════════════════

def save_episode(
    user_id: str,
    journey_id: str,
    messages: list[dict[str, str]],
    query: str,
    reply: str,
    analysis: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Save a conversation episode to episodic memory."""
    if not user_id or not query:
        return None
    analysis = analysis or {}
    # Extract simple topics from query words
    words = [w.lower() for w in re.findall(r"[a-zA-Z]{4,}", query) if w.lower() not in {
        "what", "when", "where", "which", "about", "would", "could", "should",
        "please", "help", "want", "need", "know", "like", "this", "that", "have",
    }]
    doc = {
        "user_id": user_id,
        "journey_id": journey_id,
        "query": query[:300],
        "summary": (analysis.get("summary") or query)[:300],
        "topics": list(dict.fromkeys(words))[:10],
        "domain": analysis.get("domain", "general"),
        "intent": analysis.get("intent", "question"),
        "reply_excerpt": reply[:300],
        "message_count": len(messages),
        "created_at": _now(),
    }
    client = get_mongo()
    if client is None:
        return {"name": "episodic", "label": "Episodic", "store": "mongodb", "wrote": False, "detail": "MongoDB unavailable"}
    try:
        client[MONGO_DB][EPISODIC_COLLECTION].insert_one(doc)
        return {
            "name": "episodic",
            "label": "Episodic",
            "store": "mongodb",
            "wrote": True,
            "when": _now(),
            "detail": f"Saved episode: {query[:50]}",
        }
    except Exception as exc:
        return {"name": "episodic", "label": "Episodic", "store": "mongodb", "wrote": False, "detail": str(exc)}


def load_recent_episodes(user_id: str, limit: int = 5) -> list[dict[str, Any]]:
    """Get most recent episodes for this user."""
    client = get_mongo()
    if client is None or not user_id:
        return []
    try:
        docs = list(
            client[MONGO_DB][EPISODIC_COLLECTION]
            .find({"user_id": user_id}, {"_id": 0})
            .sort("created_at", -1)
            .limit(limit)
        )
        return docs
    except Exception:
        return []


def load_relevant_episodes(user_id: str, query: str, limit: int = 3) -> list[dict[str, Any]]:
    """Search episodes by keyword scoring against summary and topics."""
    client = get_mongo()
    if client is None or not user_id or not query:
        return []
    try:
        docs = list(
            client[MONGO_DB][EPISODIC_COLLECTION]
            .find({"user_id": user_id}, {"_id": 0})
            .sort("created_at", -1)
            .limit(30)
        )
        words = [w.lower() for w in re.findall(r"[a-zA-Z]{3,}", query)]
        scored = []
        for doc in docs:
            blob = " ".join([
                str(doc.get("summary", "")),
                str(doc.get("query", "")),
                " ".join(doc.get("topics", [])),
                str(doc.get("domain", "")),
            ]).lower()
            score = sum(1 for w in words if w in blob)
            if score > 0:
                scored.append({**doc, "_score": score})
        scored.sort(key=lambda x: (x["_score"], x.get("created_at", "")), reverse=True)
        return scored[:limit]
    except Exception:
        return []


def format_episodes(episodes: list[dict[str, Any]]) -> str:
    """Format episodes into text for agent prompts."""
    if not episodes:
        return ""
    parts = []
    for ep in episodes:
        when = ep.get("created_at", "")
        try:
            dt = datetime.fromisoformat(when)
            date_str = dt.strftime("%b %d, %Y")
        except Exception:
            date_str = when[:10] if when else "recently"
        domain = ep.get("domain", "general")
        summary = ep.get("summary") or ep.get("query", "")
        parts.append(f"- On {date_str} ({domain}): {summary}")
    return "\n".join(parts)


def list_episodes(user_id: str, limit: int = 20) -> list[dict[str, Any]]:
    """List all episodes for frontend display."""
    return load_recent_episodes(user_id, limit)


# ═════════════════════════════════════════════════════════════════
# PROCEDURAL MEMORY — user preferences and behavioral patterns
# ═════════════════════════════════════════════════════════════════

_LANG_PATTERNS = [
    re.compile(r"\b(hindi|marathi|tamil|telugu|bengali|gujarati|kannada|malayalam|punjabi|urdu)\b", re.IGNORECASE),
    re.compile(r"[\u0900-\u097F\u0980-\u09FF\u0A00-\u0A7F\u0B00-\u0B7F]"),  # Devanagari/Bengali/etc.
]
_TONE_PATTERNS = [
    (re.compile(r"\b(formal|professional|official)\b", re.IGNORECASE), "formal"),
    (re.compile(r"\b(simple|easy|plain|basic|explain simply)\b", re.IGNORECASE), "simple"),
    (re.compile(r"\b(detailed|thorough|comprehensive|in.depth)\b", re.IGNORECASE), "detailed"),
    (re.compile(r"\b(short|brief|concise|quick)\b", re.IGNORECASE), "concise"),
]
_FORMAT_PATTERNS = [
    (re.compile(r"\b(bullet|points|list)\b", re.IGNORECASE), "bullet_points"),
    (re.compile(r"\b(step.by.step|steps|numbered)\b", re.IGNORECASE), "numbered_steps"),
    (re.compile(r"\b(table|comparison|compare)\b", re.IGNORECASE), "tables"),
    (re.compile(r"\b(example|sample|illustrat)\b", re.IGNORECASE), "with_examples"),
]
_JURIS_PATTERNS = [
    re.compile(r"\b(india|indian|bharat)\b", re.IGNORECASE),
    re.compile(r"\b(usa|united states|american|us law)\b", re.IGNORECASE),
    re.compile(r"\b(uk|united kingdom|british|english law)\b", re.IGNORECASE),
]


def update_procedural_memory(
    user_id: str,
    messages: list[dict[str, str]],
    query: str,
) -> dict[str, Any] | None:
    """Detect and update user preferences from conversation."""
    if not user_id or not query:
        return None
    client = get_mongo()
    if client is None:
        return None
    updates: dict[str, Any] = {}

    # Language detection
    for pat in _LANG_PATTERNS:
        if pat.search(query):
            m = _LANG_PATTERNS[0].search(query)
            updates["language"] = m.group(1).lower() if m else "regional"
            break

    # Tone detection
    for pat, tone in _TONE_PATTERNS:
        if pat.search(query):
            updates["tone"] = tone
            break

    # Format detection
    for pat, fmt in _FORMAT_PATTERNS:
        if pat.search(query):
            updates["format"] = fmt
            break

    # Jurisdiction detection
    for pat in _JURIS_PATTERNS:
        if pat.search(query):
            m = pat.search(query)
            updates["jurisdiction"] = m.group(0).capitalize()
            break

    if not updates:
        return None
    try:
        col = client[MONGO_DB][PROCEDURAL_COLLECTION]
        existing = col.find_one({"user_id": user_id}, {"_id": 0}) or {}
        for key, value in updates.items():
            existing[key] = value
        existing["user_id"] = user_id
        existing["updated_at"] = _now()
        if "created_at" not in existing:
            existing["created_at"] = _now()
        # Track interaction count
        existing["interaction_count"] = existing.get("interaction_count", 0) + 1
        col.update_one({"user_id": user_id}, {"$set": existing}, upsert=True)
        return {
            "name": "procedural",
            "label": "Procedural",
            "store": "mongodb",
            "wrote": True,
            "when": _now(),
            "detail": f"Updated preferences: {list(updates.keys())}",
        }
    except Exception as exc:
        return {"name": "procedural", "label": "Procedural", "store": "mongodb", "wrote": False, "detail": str(exc)}


def load_procedural_memory(user_id: str) -> tuple[str, dict[str, Any]]:
    """Load user preferences and return (formatted_text, report)."""
    report = {
        "name": "procedural",
        "label": "Procedural",
        "store": "mongodb",
        "used": False,
        "status": "miss",
        "when": _now(),
        "detail": "No preferences stored",
    }
    if not user_id:
        return "", report
    client = get_mongo()
    if client is None:
        return "", report
    try:
        col = client[MONGO_DB][PROCEDURAL_COLLECTION]
        doc = col.find_one({"user_id": user_id}, {"_id": 0})
        if not doc:
            return "", report
        parts = []
        if doc.get("language"):
            parts.append(f"Preferred language: {doc['language']}")
        if doc.get("tone"):
            parts.append(f"Preferred tone: {doc['tone']}")
        if doc.get("format"):
            parts.append(f"Preferred format: {doc['format']}")
        if doc.get("jurisdiction"):
            parts.append(f"Default jurisdiction: {doc['jurisdiction']}")
        if doc.get("interaction_count"):
            parts.append(f"Total interactions: {doc['interaction_count']}")
        text = "\n".join(parts) if parts else ""
        report["used"] = bool(text)
        report["status"] = "hit" if text else "miss"
        report["detail"] = f"Preferences: {', '.join(k for k in ['language','tone','format','jurisdiction'] if doc.get(k))}" if text else "No preferences"
        return text, report
    except Exception:
        return "", report


def get_procedural_memory(user_id: str) -> dict[str, Any] | None:
    """Get raw procedural memory document."""
    if not user_id:
        return None
    client = get_mongo()
    if client is None:
        return None
    try:
        return client[MONGO_DB][PROCEDURAL_COLLECTION].find_one(
            {"user_id": user_id}, {"_id": 0}
        )
    except Exception:
        return None


# ═════════════════════════════════════════════════════════════════
# CONTEXT COMPRESSION — summarize long conversations (Claude-style)
# ═════════════════════════════════════════════════════════════════

def compress_history(
    messages: list[dict[str, str]],
    api_key: str,
    model: str,
    max_messages: int = 8,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    """Compress old messages into a summary if conversation is too long.

    Returns (compressed_messages, report).
    Like Claude's approach: keeps recent messages, summarizes older ones.
    """
    report = {
        "name": "compression",
        "label": "Context compression",
        "used": False,
        "status": "skip",
        "when": _now(),
        "original_count": len(messages),
        "compressed_count": 0,
        "detail": "No compression needed",
    }
    if len(messages) <= max_messages:
        return messages, report
    old = messages[:-max_messages]
    recent = messages[-max_messages:]
    # Build a conversation text from old messages
    old_text = "\n".join(
        f"{m.get('role', 'user')}: {(m.get('content') or '')[:200]}"
        for m in old
        if (m.get("content") or "").strip()
    )
    if not old_text.strip():
        return messages, report
    # Use Groq to summarize
    try:
        from langchain_groq import ChatGroq
        from langchain_core.messages import SystemMessage, HumanMessage
        llm = ChatGroq(
            api_key=api_key, model=model,
            temperature=0.1, max_tokens=300, streaming=False,
        )
        result = llm.invoke([
            SystemMessage(content=(
                "Summarize the following conversation into 2-3 concise sentences. "
                "Focus on: what the user asked about, key topics discussed, and any "
                "important facts or decisions. Do NOT include greetings or filler."
            )),
            HumanMessage(content=old_text[:3000]),
        ])
        summary = str(result.content or "").strip()
        if not summary:
            return messages, report
        compressed = [
            {"role": "user", "content": f"[Earlier conversation summary: {summary}]"},
            {"role": "assistant", "content": "I remember our earlier discussion. Let me continue helping you."},
            *recent,
        ]
        report.update(
            used=True,
            status="done",
            original_count=len(messages),
            compressed_count=len(old),
            summary=summary[:200],
            detail=f"Compressed {len(old)} old messages into summary, kept {len(recent)} recent",
        )
        return compressed, report
    except Exception as exc:
        # Fallback: just return recent messages without summary
        report["status"] = "error"
        report["detail"] = f"Compression failed: {exc}, returning last {max_messages} messages"
        return recent, report
