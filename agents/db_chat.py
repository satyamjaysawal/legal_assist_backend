"""DB Chat Agent — text-to-SQL over Neon Postgres.

Flow:
    user question → LLM writes a SELECT (PostgreSQL dialect, schema-aware)
                  → SQL is validated (read-only, single statement, row cap)
                  → executed against Neon Postgres
                  → LLM turns the fetched rows into a readable answer

The SQL + row count are surfaced in agent_metadata so the UI can show
exactly which query was generated and how many rows came back.
"""

import json
import logging
import re
from typing import Any

from langchain_core.runnables import RunnableConfig

from agents.base import (
    AgentState,
    invoke_text,
    latest_user_text,
    register_agent,
    to_lc_messages,
)

logger = logging.getLogger("legal_assist.agents")

_llm_cache: dict[str, Any] = {}

SQL_GEN_SYSTEM = """You are the SQL generator of a legal-data assistant.
The user asks questions about a PostgreSQL database. Write ONE read-only
SELECT query (PostgreSQL dialect) that answers the question.

Database schema:
{schema}

Rules:
- Return ONLY JSON: {{"sql": "<single SELECT statement>"}}
- SELECT/WITH only — never INSERT/UPDATE/DELETE/DDL.
- Use ILIKE for fuzzy text matching; trim city/specialisation comparisons.
- Prefer useful columns and sensible ORDER BY (e.g. rating DESC, experience_years DESC).
- Keep it under LIMIT 25 rows.
- If the question cannot be answered from the schema, return {{"sql": ""}}.

No markdown, no explanations."""

ANSWER_SYSTEM = """You are the DB Chat agent of a legal AI assistant.
You answered the user's question by querying the lawyer directory database.

Generated SQL:
{sql}

Rows returned ({row_count}):
{rows}

Write a clear, friendly answer in Markdown:
- Lead with the direct answer; use a table when showing multiple records.
- Mention experience, fees, ratings, city etc. only if present in the rows.
- If zero rows came back, say so honestly and suggest broadening the filters.
- End with a one-line note that the data is sample/demo directory data.
- If the user shared their name (see profile), address them by it."""


def _get_llm(api_key: str, model: str, tag: str, temperature: float):
    cache_key = f"dbc:{tag}:{model}"
    if cache_key not in _llm_cache:
        from agents.base import get_llm
        _llm_cache[cache_key] = get_llm(api_key, model, temperature=temperature)
    return _llm_cache[cache_key]


def _generate_sql(question: str, schema: str, api_key: str, model: str, config) -> str:
    """Ask the LLM for a SELECT query; return raw SQL string (may be empty)."""
    llm = _get_llm(api_key, model, "sql", 0.0)
    system = SQL_GEN_SYSTEM.format(schema=schema)
    raw = invoke_text(llm, to_lc_messages([{"role": "user", "content": question}], system), config)
    fenced = re.search(r"\{.*\}", raw, re.DOTALL)
    text = fenced.group(0) if fenced else raw.strip()
    try:
        data = json.loads(text)
        return str(data.get("sql") or "").strip()
    except json.JSONDecodeError:
        logger.warning("db_chat: SQL generator returned non-JSON: %r", raw[:200])
        return raw.strip()


def db_chat_generate(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    """Generate SQL from the user's question, run it, and explain the rows."""
    api_key = config.get("configurable", {}).get("api_key", "")
    model = config.get("configurable", {}).get("model", "openai/gpt-oss-120b")

    from connectors.neon_postgres import (
        get_schema,
        run_select,
        schema_ddl_text,
        validate_select_sql,
    )

    question = latest_user_text(state.get("messages") or [])
    schema = schema_ddl_text()
    if not schema:
        return {
            "reply": (
                "I could not reach the lawyer directory database right now. "
                "Please try again in a moment."
            ),
            "active_agent": "db_chat",
            "agent_metadata": {"db_chat": {"error": "schema unavailable"}},
        }

    # Step 1 — text-to-SQL
    raw_sql = _generate_sql(question, schema, api_key, model, config)
    clean_sql, err = validate_select_sql(raw_sql)
    if not clean_sql:
        logger.info("db_chat: SQL rejected (%s): %r", err, raw_sql[:200])
        return {
            "reply": (
                "I couldn't turn that into a safe database query. "
                "Try asking about lawyers — e.g. *"
                "Show top-rated criminal lawyers in Delhi with 10+ years experience*."
            ),
            "active_agent": "db_chat",
            "agent_metadata": {"db_chat": {"sql": raw_sql[:500], "error": err}},
        }
    logger.info("db_chat executing SQL: %s", clean_sql)

    # Step 2 — execute against Neon
    try:
        result = run_select(clean_sql)
    except Exception as exc:
        logger.warning("db_chat query failed: %s", exc)
        return {
            "reply": "The database query failed — please rephrase or try again shortly.",
            "active_agent": "db_chat",
            "agent_metadata": {"db_chat": {"sql": clean_sql, "error": str(exc)[:300]}},
        }

    rows = result["rows"]
    rows_json = json.dumps(rows, indent=2, ensure_ascii=False, default=str)

    # Step 3 — turn rows into a readable answer
    system = ANSWER_SYSTEM.format(sql=clean_sql, row_count=result["row_count"], rows=rows_json)
    profile = (state.get("user_profile") or "").strip()
    if profile:
        system += "\n\nUser profile:\n" + profile

    llm = _get_llm(api_key, model, "answer", 0.4)
    reply = invoke_text(llm, to_lc_messages(state.get("messages") or [], system), config)

    return {
        "reply": reply,
        "active_agent": "db_chat",
        "agent_metadata": {
            "db_chat": {
                "sql": clean_sql,
                "row_count": result["row_count"],
                "columns": result["columns"],
                "tables": sorted(get_schema().keys()),
            }
        },
    }


register_agent(
    "db_chat",
    description="Answers questions about the lawyer directory by generating SQL against Neon Postgres",
    handles=["db_query"],
)
