import json
import os
import re
from datetime import datetime, timezone
from typing import Any

STM_TTL_SECONDS = int(os.getenv("REDIS_TTL_SECONDS", "21600"))
STM_WINDOW = 20
LTM_LIMIT = 8
MONGO_DB = os.getenv("MONGODB_DB", "legal_assist_inhouse")
LTM_COLLECTION = "long_term_memory"

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
    notes = ""
    if facts:
        notes = "\n".join(
            f"- ({item['domain']}) {item['summary']}"
            for item in facts
            if item.get("summary")
        )
    return {
        "history": history,
        "facts": facts,
        "notes": notes,
        "layers": [inmem_report, stm_report, thread_report, ltm_report],
    }


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
):
    return [
        save_in_memory(user_id, journey_id, messages),
        save_short_term(user_id, journey_id, messages),
        save_thread(user_id, journey_id, messages, query),
        save_long_term(user_id, journey_id, query, reply, analysis),
    ]
