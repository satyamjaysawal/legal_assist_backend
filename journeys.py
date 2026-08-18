from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import HTTPException

from memory import MONGO_DB, get_mongo

JOURNEYS = "journeys"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def journeys_col():
    client = get_mongo()
    if client is None:
        raise HTTPException(status_code=503, detail="MongoDB is not available")
    col = client[MONGO_DB][JOURNEYS]
    col.create_index([("user_id", 1), ("updated_at", -1)])
    col.create_index("journey_id", unique=True)
    return col


def _clean_messages(messages: list[dict[str, str]] | None) -> list[dict[str, str]]:
    cleaned: list[dict[str, str]] = []
    for item in messages or []:
        role = item.get("role")
        content = (item.get("content") or "").strip()
        if role in {"user", "assistant"} and content:
            cleaned.append({"role": role, "content": content})
    return cleaned


def public_journey(doc: dict[str, Any], include_messages: bool = False) -> dict[str, Any]:
    messages = _clean_messages(doc.get("messages"))
    payload = {
        "journey_id": doc.get("journey_id"),
        "user_id": doc.get("user_id"),
        "title": doc.get("title") or "New chat",
        "title_locked": bool(doc.get("title_locked")),
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
        "message_count": len(messages),
    }
    if include_messages:
        payload["messages"] = messages
    return payload


def create_journey(user_id: str, title: str = "") -> dict[str, Any]:
    col = journeys_col()
    now = _now()
    doc = {
        "journey_id": str(uuid4()),
        "user_id": user_id,
        "title": (title or "New chat").strip()[:80],
        "title_locked": False,
        "messages": [],
        "created_at": now,
        "updated_at": now,
    }
    col.insert_one(doc)
    return public_journey(doc, include_messages=True)


def list_journeys(user_id: str) -> list[dict[str, Any]]:
    col = journeys_col()
    docs = col.find({"user_id": user_id}).sort("updated_at", -1)
    return [public_journey(doc) for doc in docs]


def get_journey(user_id: str, journey_id: str) -> dict[str, Any]:
    col = journeys_col()
    doc = col.find_one({"user_id": user_id, "journey_id": journey_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Journey not found")
    return public_journey(doc, include_messages=True)


def save_journey_thread(
    user_id: str,
    journey_id: str,
    messages: list[dict[str, str]],
    title_hint: str = "",
) -> dict[str, Any]:
    col = journeys_col()
    cleaned = _clean_messages(messages)
    doc = col.find_one({"user_id": user_id, "journey_id": journey_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Journey not found")
    title = doc.get("title") or "New chat"
    locked = bool(doc.get("title_locked"))
    auto = (title_hint or "").strip()
    if not locked and auto and title in {"New chat", "New thread", "First thread"}:
        title = auto[:80]
    col.update_one(
        {"user_id": user_id, "journey_id": journey_id},
        {"$set": {"messages": cleaned, "title": title, "updated_at": _now()}},
    )
    return {"wrote": True, "turns": len(cleaned), "title": title}


def rename_journey(user_id: str, journey_id: str, title: str) -> dict[str, Any]:
    title = (title or "").strip()[:80]
    if not title:
        raise HTTPException(status_code=400, detail="Title cannot be empty")
    col = journeys_col()
    result = col.update_one(
        {"user_id": user_id, "journey_id": journey_id},
        {"$set": {"title": title, "title_locked": True, "updated_at": _now()}},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Journey not found")
    return get_journey(user_id, journey_id)


def delete_journey(user_id: str, journey_id: str) -> dict[str, Any]:
    col = journeys_col()
    doc = col.find_one({"user_id": user_id, "journey_id": journey_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Journey not found")
    cleaned = {"docs": 0, "memory": {}}
    try:
        from memory import clear_journey_memory
        from vectordb import delete_docs_for_journey

        cleaned["docs"] = delete_docs_for_journey(user_id, journey_id)
        cleaned["memory"] = clear_journey_memory(user_id, journey_id)
    except Exception:
        pass
    col.delete_one({"user_id": user_id, "journey_id": journey_id})
    remaining = list_journeys(user_id)
    next_journey = remaining[0] if remaining else create_journey(user_id, "New chat")
    return {
        "ok": True,
        "deleted": journey_id,
        "cleaned": cleaned,
        "journey": next_journey,
        "journeys": remaining if remaining else [next_journey],
    }


def delete_all_journeys(user_id: str) -> dict[str, Any]:
    """Delete every chat and its associated document/vector/memory data."""
    col = journeys_col()
    existing = list(col.find({"user_id": user_id}, {"journey_id": 1}))
    journey_ids = [item.get("journey_id") for item in existing if item.get("journey_id")]
    cleaned_docs = 0
    cleaned_memory = 0
    try:
        from memory import clear_journey_memory
        from vectordb import delete_docs_for_journey

        for journey_id in journey_ids:
            try:
                cleaned_docs += delete_docs_for_journey(user_id, journey_id)
            finally:
                memory_result = clear_journey_memory(user_id, journey_id)
                cleaned_memory += int(bool(memory_result.get("in_memory")))
    except Exception:
        pass
    result = col.delete_many({"user_id": user_id})
    journey = create_journey(user_id, "New chat")
    return {
        "ok": True,
        "deleted_count": int(result.deleted_count),
        "cleaned": {"docs": cleaned_docs, "memory": cleaned_memory},
        "journey": journey,
        "journeys": [journey],
    }


def load_journey_messages(user_id: str, journey_id: str) -> list[dict[str, str]]:
    col = journeys_col()
    doc = col.find_one({"user_id": user_id, "journey_id": journey_id})
    if not doc:
        return []
    return _clean_messages(doc.get("messages"))
