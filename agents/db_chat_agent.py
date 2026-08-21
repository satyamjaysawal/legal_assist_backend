"""DB Chat Agent — text-to-SQL over Neon Postgres (agentic).

Agentic flow (LangChain bind_tools + LangGraph tool loop):
    user question → the LLM reads the schema, writes a SELECT and calls
                    the query_lawyer_database tool with it
                  → the tool validates (read-only, single statement,
                    row cap) and executes against Neon Postgres
                  → rows come back as a ToolMessage
                  → the LLM turns the fetched rows into a readable answer

The executed SQL + row count are lifted from the tool's ToolMessage
payload into agent_metadata so the UI can show exactly which query was
generated and how many rows came back — same SSE contract as before
(main.py emits the `sql` event from it).

If the model never calls the tool (or tool-calling is unavailable),
the agent degrades to the classic deterministic text-to-SQL pipeline.
"""

import json
import logging
import re
from typing import Any

from langchain_core.runnables import RunnableConfig

from agents.tool_loop_runner import run_agent_with_tools
from agents.base import (
    AgentState,
    invoke_text,
    latest_user_text,
    register_agent,
    to_lc_messages,
)
from agents.agent_tools import DB_TOOLS

logger = logging.getLogger("legal_assist.agents")

_llm_cache: dict[str, Any] = {}

DB_CHAT_SYSTEM = """You are the DB Chat agent of a legal AI assistant.
You answer questions about the lawyer directory by querying a PostgreSQL
database with the query_lawyer_database tool.

Database schema:
{schema}

Workflow:
1. Understand the user's question.
2. Write ONE read-only SELECT (PostgreSQL dialect) using the schema above.
   - Use ILIKE for fuzzy text matching; trim city/specialisation values.
   - Prefer useful columns and sensible ORDER BY (rating DESC, experience_years DESC).
   - Keep it under LIMIT 25.
3. MANDATORY: call the query_lawyer_database tool with your SQL before
   answering. Do NOT invent rows or answer from memory — only use the
   rows the tool returns.
4. If the tool returns an error or zero rows, fix the query and retry once
   (e.g. broaden filters); otherwise explain honestly.
5. Write the final answer in friendly Markdown:
   - Lead with the direct answer; use a table when showing multiple records.
   - Mention experience, fees, ratings, city etc. only if present in the rows.
   - End with a one-line note that the data is sample/demo directory data.
   - If the user shared their name (see profile), address them by it.
- Output ONLY the final answer — never include reasoning or thinking traces."""

# Deterministic fallback prompts (used when the model cannot use tools)
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
- If the user shared their name (see profile), address them by it.
- Output ONLY the final answer — never include reasoning or thinking traces."""


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
    # Preferred: JSON {"sql": "..."}
    fenced = re.search(r"\{.*\}", raw, re.DOTALL)
    if fenced:
        try:
            data = json.loads(fenced.group(0))
            return str(data.get("sql") or "").strip()
        except json.JSONDecodeError:
            pass
    # Fallbacks: ```sql fence, or a bare SELECT/WITH statement
    fence = re.search(r"```(?:sql)?\s*(.+?)```", raw, re.DOTALL | re.IGNORECASE)
    if fence:
        return fence.group(1).strip()
    stripped = raw.strip()
    if re.match(r"^(select|with)\b", stripped, re.IGNORECASE):
        return stripped
    logger.warning("db_chat: SQL generator returned unparsable output: %r", raw[:200])
    return stripped


def _fallback_pipeline(state: AgentState, config: RunnableConfig, schema: str, api_key: str, model: str) -> dict[str, Any]:
    """Classic deterministic text-to-SQL pipeline (no tool-calling)."""
    from connectors.neon_postgres import get_schema, run_select, validate_select_sql  # noqa: PLC0415

    question = latest_user_text(state.get("messages") or [])
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
            # cache_error: failed attempts must never be cached, otherwise the
            # exact-match cache replays the failure for 6 hours.
            "agent_metadata": {"db_chat": {"sql": raw_sql[:500], "error": err, "cache_error": True}},
        }
    logger.info("db_chat executing SQL (fallback pipeline): %s", clean_sql)

    try:
        result = run_select(clean_sql)
    except Exception as exc:
        logger.warning("db_chat query failed: %s", exc)
        return {
            "reply": "The database query failed — please rephrase or try again shortly.",
            "active_agent": "db_chat",
            "agent_metadata": {"db_chat": {"sql": clean_sql, "error": str(exc)[:300], "cache_error": True}},
        }

    rows_json = json.dumps(result["rows"], indent=2, ensure_ascii=False, default=str)
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


def db_chat_generate(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    """Agentic text-to-SQL: the model calls query_lawyer_database itself."""
    api_key = config.get("configurable", {}).get("api_key", "")
    model = config.get("configurable", {}).get("model", "openai/gpt-oss-120b")

    from connectors.neon_postgres import get_schema, schema_ddl_text  # noqa: PLC0415

    schema = schema_ddl_text()
    if not schema:
        return {
            "reply": (
                "I could not reach the lawyer directory database right now. "
                "Please try again in a moment."
            ),
            "active_agent": "db_chat",
            "agent_metadata": {"db_chat": {"error": "schema unavailable", "cache_error": True}},
        }

    # ── Agentic path: LLM decides the SQL and calls the tool ─────
    system = DB_CHAT_SYSTEM.format(schema=schema)
    profile = (state.get("user_profile") or "").strip()
    if profile:
        system += "\n\nUser profile:\n" + profile

    result = run_agent_with_tools(
        system,
        state.get("messages") or [],
        DB_TOOLS,
        config,
        temperature=0.2,
        max_iterations=3,
    )
    db_info = (result.get("tool_payloads") or {}).get("query_lawyer_database")

    # Degraded (no tool support) or the model never called the tool →
    # fall back to the deterministic pipeline so SQL always gets executed.
    if not result["agentic"] or db_info is None:
        logger.info("db_chat: no tool query recorded (agentic=%s) — using fallback pipeline", result["agentic"])
        return _fallback_pipeline(state, config, schema, api_key, model)

    return {
        "reply": result["reply"],
        "active_agent": "db_chat",
        "agent_metadata": {
            "db_chat": {
                "sql": db_info["sql"],
                "row_count": db_info["row_count"],
                "columns": db_info["columns"],
                "tables": sorted(get_schema().keys()),
                "agentic": True,
                "tools_used": [t["tool"] for t in result["tool_trace"]],
            }
        },
    }


register_agent(
    "db_chat",
    description="Answers questions about the lawyer directory by generating SQL against Neon Postgres",
    handles=["db_query"],
)
