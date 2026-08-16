import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")
load_dotenv(Path(__file__).resolve().parent.parent / "legal_assist" / ".env")

from qdrant_client import QdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse
from qdrant_client.models import (
    Distance,
    Document,
    FieldCondition,
    Filter,
    MatchValue,
    PayloadSchemaType,
    PointStruct,
    VectorParams,
)

from embeddings import EMBED_DIM, GROQ_EMBED_MODEL, embed_texts
from memory import MONGO_DB, get_mongo

QDRANT_URL = os.getenv("QUDRANT_CLUSTER_ENDPOINT") or os.getenv("QDRANT_URL") or ""
QDRANT_API_KEY = os.getenv("QUDRANT_VECTOR_DB_API_KEY") or os.getenv("QDRANT_API_KEY") or ""
COLLECTION = os.getenv("QDRANT_COLLECTION", "legal_assist_docs")
CLOUD_COLLECTION = os.getenv("QDRANT_CLOUD_COLLECTION", "legal_assist_docs_cloud")
CLOUD_EMBED_MODEL = os.getenv("QDRANT_CLOUD_EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
CLOUD_EMBED_DIM = int(os.getenv("QDRANT_CLOUD_EMBED_DIM", "384"))
DOCS_COLLECTION = "documents"
SEARCH_LIMIT = int(os.getenv("QDRANT_SEARCH_LIMIT", "5"))

_client: QdrantClient | None = None
_qdrant_error = ""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_qdrant() -> QdrantClient | None:
    global _client, _qdrant_error
    if _client is not None:
        return _client
    if not QDRANT_URL or not QDRANT_API_KEY:
        _qdrant_error = "QUDRANT_CLUSTER_ENDPOINT or QUDRANT_VECTOR_DB_API_KEY is not set"
        return None
    try:
        client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY, timeout=30)
        client.get_collections()
        _client = client
        _qdrant_error = ""
        return _client
    except Exception as exc:
        _qdrant_error = str(exc)
        return None


def qdrant_status() -> dict[str, Any]:
    client = get_qdrant()
    points = 0
    if client is not None:
        try:
            info = client.get_collection(COLLECTION)
            points = int(getattr(info, "points_count", 0) or 0)
        except Exception:
            points = 0
    return {
        "ok": client is not None,
        "store": "qdrant",
        "host": QDRANT_URL,
        "collection": COLLECTION,
        "points": points,
        "embed_model": GROQ_EMBED_MODEL,
        "error": _qdrant_error,
    }


def ensure_collection(name: str = COLLECTION, dim: int = EMBED_DIM) -> QdrantClient:
    client = get_qdrant()
    if client is None:
        raise RuntimeError(_qdrant_error or "Qdrant is unavailable")
    if not client.collection_exists(name):
        client.create_collection(
            collection_name=name,
            vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
        )
    for field in ("user_id", "journey_id", "doc_id"):
        try:
            client.create_payload_index(
                collection_name=name,
                field_name=field,
                field_schema=PayloadSchemaType.KEYWORD,
            )
        except Exception:
            pass
    return client


def _cloud_client() -> QdrantClient:
    if not QDRANT_URL or not QDRANT_API_KEY:
        raise RuntimeError("Qdrant is unavailable")
    return QdrantClient(
        url=QDRANT_URL,
        api_key=QDRANT_API_KEY,
        timeout=30,
        cloud_inference=True,
    )


def _docs_col():
    mongo = get_mongo()
    if mongo is None:
        return None
    return mongo[MONGO_DB][DOCS_COLLECTION]


def save_doc_meta(meta: dict[str, Any]) -> None:
    col = _docs_col()
    if col is None:
        return
    col.update_one({"doc_id": meta["doc_id"]}, {"$set": meta}, upsert=True)


def list_docs(user_id: str, journey_id: str = "") -> list[dict[str, Any]]:
    col = _docs_col()
    if col is None:
        return []
    query: dict[str, Any] = {"user_id": user_id}
    if journey_id:
        query["journey_id"] = journey_id
    rows = list(col.find(query, {"_id": 0}).sort("created_at", -1).limit(50))
    return rows


def delete_doc(user_id: str, doc_id: str) -> dict[str, Any]:
    client = ensure_collection()
    selector = Filter(
        must=[
            FieldCondition(key="user_id", match=MatchValue(value=user_id)),
            FieldCondition(key="doc_id", match=MatchValue(value=doc_id)),
        ]
    )
    for name in (COLLECTION, CLOUD_COLLECTION):
        try:
            if client.collection_exists(name):
                client.delete(collection_name=name, points_selector=selector)
        except Exception:
            pass
    col = _docs_col()
    if col is not None:
        col.delete_one({"user_id": user_id, "doc_id": doc_id})
    return {"ok": True, "doc_id": doc_id}


def ingest_document(
    user_id: str,
    journey_id: str,
    parsed: dict[str, Any],
    chunks: list[str],
) -> dict[str, Any]:
    doc_id = str(uuid.uuid4())
    created = _now()
    embed_report: dict[str, Any]
    collection = COLLECTION
    try:
        client = ensure_collection()
        vectors, embed_report = embed_texts(chunks, kind="document")
        points = [
            PointStruct(
                id=str(uuid.uuid4()),
                vector=vector,
                payload={
                    "user_id": user_id,
                    "journey_id": journey_id,
                    "doc_id": doc_id,
                    "filename": parsed["filename"],
                    "kind": parsed["kind"],
                    "chunk_index": index,
                    "text": chunk,
                    "created_at": created,
                },
            )
            for index, (chunk, vector) in enumerate(zip(chunks, vectors))
        ]
        client.upsert(collection_name=COLLECTION, points=points, wait=True)
    except Exception as exc:
        client = ensure_collection(CLOUD_COLLECTION, CLOUD_EMBED_DIM)
        cloud = _cloud_client()
        points = [
            PointStruct(
                id=str(uuid.uuid4()),
                vector=Document(text=chunk, model=CLOUD_EMBED_MODEL),
                payload={
                    "user_id": user_id,
                    "journey_id": journey_id,
                    "doc_id": doc_id,
                    "filename": parsed["filename"],
                    "kind": parsed["kind"],
                    "chunk_index": index,
                    "text": chunk,
                    "created_at": created,
                },
            )
            for index, chunk in enumerate(chunks)
        ]
        cloud.upsert(collection_name=CLOUD_COLLECTION, points=points, wait=True)
        collection = CLOUD_COLLECTION
        embed_report = {
            "name": "embeddings",
            "label": "Embeddings",
            "model": CLOUD_EMBED_MODEL,
            "provider": "qdrant_cloud",
            "status": "hit",
            "detail": f"Qdrant Cloud {CLOUD_EMBED_MODEL} after local embed failed: {exc}",
        }
    meta = {
        "doc_id": doc_id,
        "user_id": user_id,
        "journey_id": journey_id,
        "filename": parsed["filename"],
        "kind": parsed["kind"],
        "content_type": parsed.get("content_type") or "",
        "bytes": parsed.get("bytes") or 0,
        "chars": parsed.get("chars") or 0,
        "chunks": len(chunks),
        "embed_model": embed_report.get("model") or GROQ_EMBED_MODEL,
        "embed_provider": embed_report.get("provider") or "",
        "collection": collection,
        "created_at": created,
    }
    save_doc_meta(meta)
    return {
        **meta,
        "embed": embed_report,
        "preview": chunks[0][:240] if chunks else "",
    }


def search_docs(
    user_id: str,
    query: str,
    journey_id: str = "",
    limit: int = SEARCH_LIMIT,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    report = {
        "name": "qdrant",
        "label": "Qdrant RAG",
        "store": "qdrant",
        "model": GROQ_EMBED_MODEL,
        "status": "miss",
        "used": False,
        "hits": 0,
        "detail": "No matching document chunks",
    }
    if not query.strip():
        return [], report
    client = get_qdrant()
    if client is None:
        report.update(status="error", detail=_qdrant_error or "Qdrant unavailable")
        return [], report
    must = [FieldCondition(key="user_id", match=MatchValue(value=user_id))]
    if journey_id:
        must.append(FieldCondition(key="journey_id", match=MatchValue(value=journey_id)))
    query_filter = Filter(must=must)

    def pack(points) -> list[dict[str, Any]]:
        hits = []
        for point in points:
            payload = point.payload or {}
            text = str(payload.get("text") or "").strip()
            if not text:
                continue
            hits.append(
                {
                    "id": str(point.id),
                    "score": round(float(point.score or 0), 4),
                    "filename": payload.get("filename") or "",
                    "kind": payload.get("kind") or "",
                    "doc_id": payload.get("doc_id") or "",
                    "chunk_index": payload.get("chunk_index"),
                    "text": text,
                }
            )
        return hits

    try:
        if client.collection_exists(COLLECTION):
            ensure_collection()
            try:
                vectors, embed_report = embed_texts([query], kind="query")
                result = client.query_points(
                    collection_name=COLLECTION,
                    query=vectors[0],
                    query_filter=query_filter,
                    limit=limit,
                    with_payload=True,
                )
                hits = pack(result.points)
                report["embed"] = embed_report
                if hits:
                    report.update(
                        status="hit",
                        used=True,
                        hits=len(hits),
                        detail=f"{len(hits)} chunk(s) from {len({h['doc_id'] for h in hits})} file(s)",
                    )
                    return hits, report
            except Exception:
                pass
        if client.collection_exists(CLOUD_COLLECTION):
            ensure_collection(CLOUD_COLLECTION, CLOUD_EMBED_DIM)
            cloud = _cloud_client()
            result = cloud.query_points(
                collection_name=CLOUD_COLLECTION,
                query=Document(text=query, model=CLOUD_EMBED_MODEL),
                query_filter=query_filter,
                limit=limit,
                with_payload=True,
            )
            hits = pack(result.points)
            report.update(model=CLOUD_EMBED_MODEL, embed={"provider": "qdrant_cloud", "model": CLOUD_EMBED_MODEL})
            if hits:
                report.update(
                    status="hit",
                    used=True,
                    hits=len(hits),
                    detail=f"{len(hits)} chunk(s) via Qdrant Cloud {CLOUD_EMBED_MODEL}",
                )
                return hits, report
        report["detail"] = "Qdrant query ran, no similar chunks"
        return [], report
    except UnexpectedResponse as exc:
        report.update(status="error", detail=str(exc))
        return [], report
    except Exception as exc:
        report.update(status="error", detail=str(exc))
        return [], report


def hits_fingerprint(hits: list[dict[str, Any]]) -> str:
    if not hits:
        return "none"
    return ",".join(item.get("id") or "" for item in hits)


def format_hits(hits: list[dict[str, Any]]) -> str:
    if not hits:
        return ""
    parts = []
    for i, hit in enumerate(hits, start=1):
        parts.append(
            f"[{i}] {hit.get('filename') or 'document'} "
            f"(score {hit.get('score')}):\n{hit.get('text')}"
        )
    return "\n\n".join(parts)
