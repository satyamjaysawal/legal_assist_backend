"""Neon Postgres connector — pooled access for the db_chat agent.

Provides:
  - get_pg_conn(): context manager yielding a psycopg2 connection
  - run_select(): executes a validated SELECT and returns dict rows
  - get_schema(): cached information_schema introspection (for text-to-SQL)
  - pg_status(): health probe for /status

Connection string comes from NEON_POSTGRE_DB (Neon pooler URL).
"""

import logging
import os
import re
from contextlib import contextmanager
from typing import Any

from connectors.base import register_connector

logger = logging.getLogger("legal_assist.postgres")

PG_URL = os.getenv("NEON_POSTGRE_DB", "")
# The db_chat agent may only ever run SELECTs; cap rows defensively.
MAX_ROWS = int(os.getenv("PG_MAX_ROWS", "25"))
QUERY_TIMEOUT_MS = int(os.getenv("PG_QUERY_TIMEOUT_MS", "10000"))

_schema_cache: dict[str, Any] = {}


def _import_psycopg2():
    try:
        import psycopg2  # noqa: PLC0415 — lazy so missing driver degrades gracefully
        import psycopg2.extras  # noqa: F401
        return psycopg2
    except ImportError:
        logger.warning("psycopg2 not installed — Neon Postgres unavailable")
        return None


@contextmanager
def get_pg_conn(read_only: bool = True):
    """Yield a Neon Postgres connection.

    read_only=True (default, used by db_chat) enforces a read-only
    transaction plus a statement timeout; seeding passes read_only=False.
    """
    psycopg2 = _import_psycopg2()
    if psycopg2 is None or not PG_URL:
        raise RuntimeError("Neon Postgres not configured (NEON_POSTGRE_DB missing or psycopg2 not installed)")
    # Neon's pooler rejects startup `options`, so the timeout is applied
    # with SET right after connecting instead.
    conn = psycopg2.connect(PG_URL, connect_timeout=10)
    try:
        with conn.cursor() as cur:
            cur.execute(f"SET statement_timeout = {QUERY_TIMEOUT_MS}")
            if read_only:
                cur.execute("SET TRANSACTION READ ONLY")
        yield conn
    finally:
        conn.close()


def run_select(sql: str, limit: int = MAX_ROWS) -> dict[str, Any]:
    """Execute a SELECT statement and return {rows, row_count, columns}."""
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            columns = [d[0] for d in cur.description] if cur.description else []
            rows = [dict(zip(columns, row)) for row in cur.fetchmany(limit)]
            return {"rows": rows, "row_count": len(rows), "columns": columns}


def get_schema(force_refresh: bool = False) -> dict[str, list[dict[str, str]]]:
    """Introspect public tables → {table: [{column, data_type}, ...]} (cached)."""
    if _schema_cache and not force_refresh:
        return _schema_cache.get("tables") or {}
    sql = (
        "SELECT table_name, column_name, data_type "
        "FROM information_schema.columns "
        "WHERE table_schema = 'public' "
        "ORDER BY table_name, ordinal_position"
    )
    try:
        result = run_select(sql, limit=500)
    except Exception as exc:
        logger.warning("Schema introspection failed: %s", exc)
        return {}
    tables: dict[str, list[dict[str, str]]] = {}
    for row in result["rows"]:
        tables.setdefault(row["table_name"], []).append(
            {"column": row["column_name"], "data_type": row["data_type"]}
        )
    _schema_cache["tables"] = tables
    logger.info("Postgres schema cached: %d table(s)", len(tables))
    return tables


def schema_ddl_text() -> str:
    """Human-readable schema block for the text-to-SQL prompt."""
    tables = get_schema()
    if not tables:
        return ""
    lines = []
    for table, cols in tables.items():
        col_list = ", ".join(f"{c['column']} {c['data_type']}" for c in cols)
        lines.append(f"{table} ({col_list})")
    return "\n".join(lines)


def pg_status() -> dict[str, Any]:
    """Cheap health probe — SELECT 1 + table count."""
    if not PG_URL:
        return {"name": "neon_postgres", "available": False, "reason": "NEON_POSTGRE_DB not set"}
    if _import_psycopg2() is None:
        return {"name": "neon_postgres", "available": False, "reason": "psycopg2 not installed"}
    try:
        result = run_select(
            "SELECT COUNT(*) AS n FROM information_schema.tables WHERE table_schema = 'public'",
            limit=1,
        )
        return {
            "name": "neon_postgres",
            "available": True,
            "tables": result["rows"][0]["n"] if result["rows"] else 0,
        }
    except Exception as exc:
        return {"name": "neon_postgres", "available": False, "reason": str(exc)[:200]}


# ── SQL safety validation (db_chat may only run read-only SELECTs) ──

_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|truncate|create|grant|revoke|copy|vacuum|reindex|refresh|call|execute|prepare)\b",
    re.IGNORECASE,
)


def validate_select_sql(sql: str) -> tuple[str | None, str]:
    """Return (clean_sql, error). clean_sql is None when the SQL is unsafe."""
    cleaned = (sql or "").strip().rstrip(";").strip()
    # strip line comments to stop smuggled keywords hiding after '--'
    cleaned = re.sub(r"--.*?$", "", cleaned, flags=re.MULTILINE).strip()
    cleaned = re.sub(r"/\*.*?\*/", "", cleaned, flags=re.DOTALL).strip()
    if not cleaned:
        return None, "Empty SQL"
    if ";" in cleaned:
        return None, "Multiple statements are not allowed"
    if not re.match(r"^\s*(select|with)\b", cleaned, re.IGNORECASE):
        return None, "Only SELECT queries are allowed"
    if _FORBIDDEN.search(cleaned):
        return None, "Query contains a forbidden write/DDL keyword"
    # enforce a row cap
    if not re.search(r"\blimit\s+\d+", cleaned, re.IGNORECASE):
        cleaned = f"{cleaned} LIMIT {MAX_ROWS}"
    else:
        cleaned = re.sub(
            r"\blimit\s+(\d+)",
            lambda m: f"LIMIT {min(int(m.group(1)), MAX_ROWS)}",
            cleaned,
            flags=re.IGNORECASE,
        )
    return cleaned, ""


class NeonPostgresConnector:
    """Connector-registry adapter so /status lists the Neon database."""

    def status(self) -> dict[str, Any]:
        return pg_status()


register_connector("neon_postgres", NeonPostgresConnector())
