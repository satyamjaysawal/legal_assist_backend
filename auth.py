import os
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import bcrypt
import jwt
from fastapi import Header, HTTPException

from memory import MONGO_DB, get_mongo

USERS = "users"
JWT_SECRET = os.getenv("JWT_SECRET", "legal-assist-change-me")
JWT_HOURS = int(os.getenv("JWT_HOURS", "168"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def users_col():
    client = get_mongo()
    if client is None:
        raise HTTPException(status_code=503, detail="MongoDB is not available")
    col = client[MONGO_DB][USERS]
    col.create_index("email", unique=True)
    return col


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def check_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def public_user(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "user_id": doc.get("user_id"),
        "email": doc.get("email"),
        "name": doc.get("name") or "",
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
    }


def make_token(user_id: str, email: str) -> str:
    payload = {
        "sub": user_id,
        "email": email,
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def register_user(email: str, password: str, name: str) -> dict[str, Any]:
    email = email.strip().lower()
    name = (name or "").strip() or email.split("@")[0]
    if "@" not in email or "." not in email.split("@")[-1]:
        raise HTTPException(status_code=400, detail="Enter a valid email")
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
    col = users_col()
    if col.find_one({"email": email}):
        raise HTTPException(status_code=409, detail="Email already registered")
    doc = {
        "user_id": str(uuid4()),
        "email": email,
        "name": name,
        "password_hash": hash_password(password),
        "created_at": _now(),
        "updated_at": _now(),
    }
    col.insert_one(doc)
    return public_user(doc)


def login_user(email: str, password: str) -> dict[str, Any]:
    email = email.strip().lower()
    col = users_col()
    doc = col.find_one({"email": email})
    if not doc or not check_password(password, doc.get("password_hash") or ""):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    return public_user(doc)


def find_user(user_id: str) -> dict[str, Any] | None:
    col = users_col()
    doc = col.find_one({"user_id": user_id})
    return public_user(doc) if doc else None


def update_user(user_id: str, name: str | None = None) -> dict[str, Any]:
    col = users_col()
    updates: dict[str, Any] = {"updated_at": _now()}
    if name is not None:
        name = name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="Name cannot be empty")
        updates["name"] = name
    col.update_one({"user_id": user_id}, {"$set": updates})
    doc = col.find_one({"user_id": user_id})
    if not doc:
        raise HTTPException(status_code=404, detail="User not found")
    return public_user(doc)


def current_user(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Login required")
    token = authorization.split(" ", 1)[1].strip()
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status_code=401, detail="Session expired") from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail="Invalid token") from exc
    user = find_user(str(payload.get("sub") or ""))
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user
