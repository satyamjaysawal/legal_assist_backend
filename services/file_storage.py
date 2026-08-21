import logging
import os
from io import BytesIO
from typing import Any

from bson import ObjectId
from gridfs import GridFS, NoFile
from pymongo.errors import PyMongoError

from services.memory_service import MONGO_DB, get_mongo

GRIDFS_BUCKET = os.getenv("MONGO_FILES_BUCKET", "files")

logger = logging.getLogger("legal_assist.services.file_storage")


def get_fs() -> GridFS:
    client = get_mongo()
    if client is None:
        raise RuntimeError("MongoDB is unavailable")
    return GridFS(client[MONGO_DB], collection=GRIDFS_BUCKET)


def files_status() -> dict[str, Any]:
    try:
        client = get_mongo()
        if client is None:
            return {"ok": False, "store": "mongodb_gridfs", "db": MONGO_DB, "bucket": GRIDFS_BUCKET}
        fs = GridFS(client[MONGO_DB], collection=GRIDFS_BUCKET)
        count = client[MONGO_DB][f"{GRIDFS_BUCKET}.files"].estimated_document_count()
        return {
            "ok": True,
            "store": "mongodb_gridfs",
            "db": MONGO_DB,
            "bucket": GRIDFS_BUCKET,
            "files": count,
            "collections": [f"{GRIDFS_BUCKET}.files", f"{GRIDFS_BUCKET}.chunks"],
        }
    except Exception as exc:
        return {"ok": False, "store": "mongodb_gridfs", "db": MONGO_DB, "error": str(exc)}


def store_original_file(
    *,
    user_id: str,
    journey_id: str,
    doc_id: str,
    filename: str,
    content_type: str,
    data: bytes,
) -> dict[str, Any]:
    fs = get_fs()
    file_id = fs.put(
        BytesIO(data),
        filename=filename,
        contentType=content_type or "application/octet-stream",
        user_id=user_id,
        journey_id=journey_id,
        doc_id=doc_id,
        bytes=len(data),
    )
    logger.info("Stored original file %s (%d bytes) as gridfs=%s", filename, len(data), file_id)
    return {
        "gridfs_id": str(file_id),
        "store": "mongodb_gridfs",
        "db": MONGO_DB,
        "bucket": GRIDFS_BUCKET,
        "filename": filename,
        "bytes": len(data),
        "detail": f"Saved {filename} ({len(data)} bytes) to {MONGO_DB}.{GRIDFS_BUCKET}",
    }


def get_original_file(user_id: str, gridfs_id: str) -> tuple[Any, dict[str, Any]]:
    fs = get_fs()
    try:
        grid_out = fs.get(ObjectId(gridfs_id))
    except (NoFile, Exception) as exc:
        raise FileNotFoundError("File not found in MongoDB") from exc
    if str(getattr(grid_out, "user_id", "") or "") != user_id:
        raise PermissionError("File does not belong to this user")
    meta = {
        "filename": grid_out.filename or "download",
        "content_type": getattr(grid_out, "contentType", None) or "application/octet-stream",
        "bytes": grid_out.length,
        "gridfs_id": gridfs_id,
    }
    return grid_out, meta


def delete_original_file(gridfs_id: str) -> None:
    if not gridfs_id:
        return
    try:
        get_fs().delete(ObjectId(gridfs_id))
    except (NoFile, PyMongoError, Exception):
        pass
