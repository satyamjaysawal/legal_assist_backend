"""DB Task Agent — general-purpose database worker over Neon Postgres.

Unlike db_chat (read-only lawyer-directory Q&A), this agent can perform
any bounded database task: schema inspection, SELECT, INSERT, UPDATE and
DELETE.  Every statement passes the write-policy guardrail
(validate_task_sql): single statement, no DDL/admin keywords, row cap on
reads, writes committed atomically with rollback on error.

Each tool result carries a `guardrails` list which is lifted into
agent_metadata so main.py can stream `guardrail` SSE events and the UI
can show exactly which guardrails passed, were enforced, or blocked.
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
from agents.agent_tools import DB_TASK_TOOLS

logger = logging.getLogger("legal_assist.agents")

_llm_cache: dict[str, Any] = {}

DB_TASK_SYSTEM = """You are the DB Task agent of a legal AI assistant.
You perform ANY bounded database task the user asks for — inspect the
schema, read data, add rows, update or delete records — by calling the
run_database_task tool (and inspect_database_schema when you need the
current schema).

Database schema:
{schema}

Workflow:
1. Understand the database task (query, insert, update, delete, inspect).
2. If the schema above is missing detail you need, call
   inspect_database_schema first.
3. Write ONE PostgreSQL statement (SELECT/INSERT/UPDATE/DELETE) and call
   run_database_task with it. DDL (DROP/ALTER/CREATE/TRUNCATE/…) and
   multiple statements are blocked by guardrails — never attempt them.
4. If the tool reports a guardrail block or a DB error, adapt once
   (fix the statement) or explain honestly which guardrail stopped it.
5. If the user asked to add data when none exists, first SELECT to
   check, then INSERT sensible sample rows, then SELECT again to show
   the result.
6. Write the final answer in friendly Markdown:
   - Lead with what you did; use a table when showing rows.
   - Mention how many rows were read/affected.
   - Note when a guardrail blocked something and why.
   - End with a one-line note that this is demo database data.
- Output ONLY the final answer — never include reasoning or thinking traces."""

SQL_GEN_SYSTEM = """You are the SQL generator of a database task agent.
The user asks for a database task on a PostgreSQL database. Write ONE
statement (SELECT/INSERT/UPDATE/DELETE — PostgreSQL dialect) that
performs the task.

Database schema:
{schema}

Rules:
- Return ONLY JSON: {{"sql": "<single statement>"}}
- Never DDL (DROP/ALTER/CREATE/TRUNCATE/…), never multiple statements.
- Reads: keep under LIMIT 25 rows.
- If the task cannot be done from the schema, return {{"sql": ""}}.

No markdown, no explanations."""

ANSWER_SYSTEM = """You are the DB Task agent of a legal AI assistant.
You performed a database task with this statement:

{sql}

Result ({kind}, {row_count} row(s)):
{rows}

Write a clear, friendly answer in Markdown:
- Lead with what happened; use a table when showing rows.
- If zero rows or an error, say so honestly and suggest a next step.
- End with a one-line note that this is demo database data.
- Output ONLY the final answer — never include reasoning or thinking traces."""


def _get_llm(api_key: str, model: str, tag: str, temperature: float):
    cache_key = f"dbtask:{tag}:{model}"
    if cache_key not in _llm_cache:
        from agents.base import get_llm
        _llm_cache[cache_key] = get_llm(api_key, model, temperature=temperature)
    return _llm_cache[cache_key]


def _generate_sql(question: str, schema: str, api_key: str, model: str, config) -> str:
    """Ask the LLM for one statement; return raw SQL string (may be empty)."""
    llm = _get_llm(api_key, model, "sql", 0.0)
    system = SQL_GEN_SYSTEM.format(schema=schema)
    raw = invoke_text(llm, to_lc_messages([{"role": "user", "content": question}], system), config)
    fenced = re.search(r"\{.*\}", raw, re.DOTALL)
    if fenced:
        try:
            data = json.loads(fenced.group(0))
            return str(data.get("sql") or "").strip()
        except json.JSONDecodeError:
            pass
    fence = re.search(r"```(?:sql)?\s*(.+?)```", raw, re.DOTALL | re.IGNORECASE)
    if fence:
        return fence.group(1).strip()
    stripped = raw.strip()
    if re.match(r"^(select|with|insert|update|delete)\b", stripped, re.IGNORECASE):
        return stripped
    logger.warning("db_task: SQL generator returned unparsable output: %r", raw[:200])
    return stripped


def _fallback_pipeline(state: AgentState, config: RunnableConfig, schema: str, api_key: str, model: str) -> dict[str, Any]:
    """Deterministic pipeline (no tool-calling): generate → validate → run."""
    from connectors.neon_postgres import get_schema, run_select, run_write, validate_task_sql  # noqa: PLC0415

    question = latest_user_text(state.get("messages") or [])
    raw_sql = _generate_sql(question, schema, api_key, model, config)
    clean_sql, err, kind = validate_task_sql(raw_sql)
    if not clean_sql:
        logger.info("db_task: SQL rejected (%s): %r", err, raw_sql[:200])
        return {
            "reply": (
                "I couldn't turn that into a safe database statement. "
                "I can run SELECT/INSERT/UPDATE/DELETE — e.g. *"
                "Add a sample lawyer row and show me the table*."
            ),
            "active_agent": "db_task",
            "agent_metadata": {"db_task": {
                "sql": raw_sql[:500], "error": err, "cache_error": True,
                "guardrails": [{"name": "write_policy", "status": "blocked", "detail": err}],
            }},
        }
    logger.info("db_task executing SQL (fallback pipeline): %s", clean_sql)

    guards = [{"name": "write_policy", "status": "passed",
               "detail": "single statement; DDL/admin keywords blocked, SELECT/INSERT/UPDATE/DELETE allowed"}]
    try:
        if kind == "select":
            result = run_select(clean_sql)
            guards.append({"name": "row_cap", "status": "enforced", "detail": "result capped at the configured row limit"})
        else:
            result = run_write(clean_sql)
            guards.append({"name": "transaction", "status": "passed", "detail": "write committed atomically (rollback on error)"})
        guards.append({"name": "statement_timeout", "status": "enforced", "detail": "query runs under a statement timeout"})
    except Exception as exc:
        logger.warning("db_task statement failed: %s", exc)
        return {
            "reply": "The database statement failed — please rephrase or try again shortly.",
            "active_agent": "db_task",
            "agent_metadata": {"db_task": {
                "sql": clean_sql, "error": str(exc)[:300], "cache_error": True,
                "guardrails": guards + [{"name": "transaction", "status": "blocked", "detail": f"rolled back after error: {str(exc)[:120]}"}],
            }},
        }

    rows_json = json.dumps(result["rows"], indent=2, ensure_ascii=False, default=str)
    system = ANSWER_SYSTEM.format(sql=clean_sql, kind=kind, row_count=result["row_count"], rows=rows_json)
    profile = (state.get("user_profile") or "").strip()
    if profile:
        system += "\n\nUser profile:\n" + profile

    llm = _get_llm(api_key, model, "answer", 0.4)
    reply = invoke_text(llm, to_lc_messages(state.get("messages") or [], system), config)

    return {
        "reply": reply,
        "active_agent": "db_task",
        "agent_metadata": {
            "db_task": {
                "sql": clean_sql,
                "kind": kind,
                "row_count": result["row_count"],
                "columns": result["columns"],
                "tables": sorted(get_schema().keys()),
                "guardrails": guards,
            }
        },
    }


def db_task_generate(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    """Agentic database worker: the model drives the tools itself."""
    api_key = config.get("configurable", {}).get("api_key", "")
    model = config.get("configurable", {}).get("model", "openai/gpt-oss-120b")

    from connectors.neon_postgres import get_schema, schema_ddl_text  # noqa: PLC0415

    schema = schema_ddl_text()
    if not schema:
        return {
            "reply": (
                "I could not reach the database right now. "
                "Please try again in a moment."
            ),
            "active_agent": "db_task",
            "agent_metadata": {"db_task": {"error": "schema unavailable", "cache_error": True}},
        }

    system = DB_TASK_SYSTEM.format(schema=schema)
    profile = (state.get("user_profile") or "").strip()
    if profile:
        system += "\n\nUser profile:\n" + profile

    result = run_agent_with_tools(
        system,
        state.get("messages") or [],
        DB_TASK_TOOLS,
        config,
        temperature=0.2,
        max_iterations=4,
    )
    db_info = (result.get("tool_payloads") or {}).get("run_database_task")

    if not result["agentic"] or db_info is None:
        logger.info("db_task: no tool statement recorded (agentic=%s) — using fallback pipeline", result["agentic"])
        return _fallback_pipeline(state, config, schema, api_key, model)

    return {
        "reply": result["reply"],
        "active_agent": "db_task",
        "agent_metadata": {
            "db_task": {
                "sql": db_info.get("sql") or "",
                "kind": db_info.get("kind") or "",
                "row_count": db_info.get("row_count") or 0,
                "columns": db_info.get("columns") or [],
                "tables": sorted(get_schema().keys()),
                "guardrails": db_info.get("guardrails") or [],
                "error": db_info.get("error") or "",
                "cache_error": bool(db_info.get("error")),
                "agentic": True,
                "tools_used": [t["tool"] for t in result["tool_trace"]],
            }
        },
    }


register_agent(
    "db_task",
    description="General-purpose database worker: schema inspection, SELECT/INSERT/UPDATE/DELETE with write-policy guardrails",
    handles=["db_task"],
)
