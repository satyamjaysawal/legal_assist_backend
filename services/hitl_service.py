"""Human-in-the-loop checkpoint store.

The HITL workflow pauses at the human approval checkpoint instead of
auto-continuing.  The paused state (stage tree, draft, counters) is saved here
so a later ``POST /chat/hitl/resume`` call — possibly from a different
serverless instance — can claim the checkpoint and continue the workflow.

Backed by MongoDB when available, with an in-memory fallback for local runs
and tests.  Checkpoints expire after a fixed TTL so stale approvals cannot be
resumed days later.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from services.memory_service import MONGO_DB, get_mongo

COLLECTION = "hitl_checkpoints"
HITL_TTL_HOURS = 24

logger = logging.getLogger("legal_assist.services.hitl")

# In-memory fallback (local dev / Mongo unavailable).
_FALLBACK: dict[str, dict[str, Any]] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _col():
    client = get_mongo()
    if client is None:
        return None
    return client[MONGO_DB][COLLECTION]


def save_checkpoint(doc: dict[str, Any]) -> None:
    """Persist a pending HITL checkpoint keyed by request_id."""
    doc = dict(doc)
    doc.setdefault("status", "pending")
    doc.setdefault("created_at", _now().isoformat())
    doc.setdefault("expires_at", (_now() + timedelta(hours=HITL_TTL_HOURS)).isoformat())
    col = _col()
    if col is not None:
        try:
            col.replace_one({"request_id": doc["request_id"]}, doc, upsert=True)
            return
        except Exception as exc:  # noqa: BLE001 - fall back to memory
            logger.warning("HITL checkpoint store (mongo) unavailable: %s", exc)
    _FALLBACK[doc["request_id"]] = doc


def claim_checkpoint(user_id: str, request_id: str) -> dict[str, Any] | None:
    """Atomically claim a pending checkpoint for this user.

    Returns the checkpoint document or ``None`` when missing, expired, owned by
    another user, or already resumed (prevents double-resume races).
    """
    col = _col()
    if col is not None:
        try:
            update = col.find_one_and_update(
                {"request_id": request_id, "user_id": user_id, "status": "pending"},
                {"$set": {"status": "resumed", "resumed_at": _now().isoformat()}},
            )
            if update and (update.get("expires_at") or "") >= _now().isoformat():
                return update
            if update:
                col.update_one({"request_id": request_id}, {"$set": {"status": "expired"}})
            return None
        except Exception as exc:  # noqa: BLE001 - fall back to memory
            logger.warning("HITL claim (mongo) failed: %s", exc)

    doc = _FALLBACK.get(request_id)
    if not doc or doc.get("user_id") != user_id or doc.get("status") != "pending":
        return None
    if (doc.get("expires_at") or "") < _now().isoformat():
        doc["status"] = "expired"
        return None
    doc["status"] = "resumed"
    doc["resumed_at"] = _now().isoformat()
    return doc
