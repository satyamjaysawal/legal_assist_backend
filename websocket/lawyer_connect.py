"""Lawyer Connect — WebSocket-based real-time chat between user and lawyer.

Sub-agent of Lawyer Finder.  Enables:
  - User sends a chat request to a specific lawyer
  - Lawyer accepts and a private WebSocket room is created
  - Messages are relayed in real-time between user ↔ lawyer
  - AI can optionally assist during the conversation (co-pilot mode)

This is a dummy/skeleton implementation.  The WebSocket infrastructure
is functional, but the lawyer-side UI and persistence are stubs.
"""

import asyncio
import json
import time
from typing import Any
from uuid import uuid4

from fastapi import WebSocket, WebSocketDisconnect

# ── In-memory room store (replace with Redis/MongoDB for production) ────
_rooms: dict[str, dict[str, Any]] = {}


def _now() -> float:
    return time.time()


def create_room(user_id: str, lawyer_id: str, journey_id: str = "") -> dict[str, Any]:
    """Create a new chat room between a user and a lawyer."""
    room_id = str(uuid4())
    room = {
        "room_id": room_id,
        "user_id": user_id,
        "lawyer_id": lawyer_id,
        "journey_id": journey_id,
        "status": "waiting",  # waiting → active → closed
        "messages": [],
        "created_at": _now(),
        "user_ws": None,
        "lawyer_ws": None,
    }
    _rooms[room_id] = room
    return {"room_id": room_id, "status": "waiting"}


def get_room(room_id: str) -> dict[str, Any] | None:
    return _rooms.get(room_id)


def list_rooms(user_id: str | None = None, lawyer_id: str | None = None) -> list[dict[str, Any]]:
    results = []
    for room in _rooms.values():
        if user_id and room["user_id"] != user_id:
            continue
        if lawyer_id and room["lawyer_id"] != lawyer_id:
            continue
        results.append({
            "room_id": room["room_id"],
            "user_id": room["user_id"],
            "lawyer_id": room["lawyer_id"],
            "status": room["status"],
            "message_count": len(room["messages"]),
            "created_at": room["created_at"],
        })
    return results


def close_room(room_id: str) -> dict[str, Any]:
    room = _rooms.get(room_id)
    if room:
        room["status"] = "closed"
    return {"room_id": room_id, "status": "closed"}


async def handle_user_websocket(websocket: WebSocket, room_id: str, user_id: str) -> None:
    """Handle WebSocket connection from the user side."""
    await websocket.accept()
    room = get_room(room_id)
    if not room:
        await websocket.send_json({"type": "error", "detail": "Room not found"})
        await websocket.close()
        return
    if room["user_id"] != user_id:
        await websocket.send_json({"type": "error", "detail": "Not authorized for this room"})
        await websocket.close()
        return

    room["user_ws"] = websocket
    room["status"] = "active"

    await websocket.send_json({
        "type": "connected",
        "room_id": room_id,
        "lawyer_id": room["lawyer_id"],
        "message": "Connected to lawyer chat. Messages will be relayed in real-time.",
    })

    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type", "message")

            if msg_type == "message":
                message = {
                    "sender": "user",
                    "text": data.get("text", ""),
                    "timestamp": _now(),
                }
                room["messages"].append(message)

                # Relay to lawyer if connected
                if room.get("lawyer_ws"):
                    try:
                        await room["lawyer_ws"].send_json({
                            "type": "message",
                            "sender": "user",
                            "text": message["text"],
                            "timestamp": message["timestamp"],
                        })
                    except Exception:
                        pass

                await websocket.send_json({
                    "type": "sent",
                    "text": message["text"],
                    "timestamp": message["timestamp"],
                })

            elif msg_type == "end_session":
                room["status"] = "closed"
                if room.get("lawyer_ws"):
                    try:
                        await room["lawyer_ws"].send_json({"type": "session_ended"})
                    except Exception:
                        pass
                await websocket.send_json({"type": "session_ended"})
                break

    except WebSocketDisconnect:
        pass
    finally:
        room["user_ws"] = None


async def handle_lawyer_websocket(websocket: WebSocket, room_id: str, lawyer_id: str) -> None:
    """Handle WebSocket connection from the lawyer side."""
    await websocket.accept()
    room = get_room(room_id)
    if not room:
        await websocket.send_json({"type": "error", "detail": "Room not found"})
        await websocket.close()
        return
    if room["lawyer_id"] != lawyer_id:
        await websocket.send_json({"type": "error", "detail": "Not authorized for this room"})
        await websocket.close()
        return

    room["lawyer_ws"] = websocket
    room["status"] = "active"

    # Send chat history
    await websocket.send_json({
        "type": "connected",
        "room_id": room_id,
        "user_id": room["user_id"],
        "history": room["messages"],
        "message": "Connected. Previous messages included above.",
    })

    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type", "message")

            if msg_type == "message":
                message = {
                    "sender": "lawyer",
                    "text": data.get("text", ""),
                    "timestamp": _now(),
                }
                room["messages"].append(message)

                # Relay to user if connected
                if room.get("user_ws"):
                    try:
                        await room["user_ws"].send_json({
                            "type": "message",
                            "sender": "lawyer",
                            "text": message["text"],
                            "timestamp": message["timestamp"],
                        })
                    except Exception:
                        pass

                await websocket.send_json({
                    "type": "sent",
                    "text": message["text"],
                    "timestamp": message["timestamp"],
                })

            elif msg_type == "end_session":
                room["status"] = "closed"
                if room.get("user_ws"):
                    try:
                        await room["user_ws"].send_json({"type": "session_ended"})
                    except Exception:
                        pass
                await websocket.send_json({"type": "session_ended"})
                break

    except WebSocketDisconnect:
        pass
    finally:
        room["lawyer_ws"] = None
