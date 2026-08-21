"""LangChain tools — agentic wrappers around the connector layer.

Pattern: every external capability (database, directory, glossary,
bare acts, templates) is exposed as an @tool function.  Agents bind
these tools to the LLM (`llm.bind_tools`) and the model decides WHEN
and WITH WHICH ARGUMENTS to call them inside a LangGraph tool loop
(see agents/tool_loop_runner.py).

To add a new use case:
  1. Build the capability as a connector (connectors/).
  2. Wrap it here with @tool — keep the return value a compact JSON
     string.  If the result must surface in the UI pipeline (like the
     executed SQL), include it in the JSON — the agent lifts it from
     run_agent_with_tools()["tool_payloads"].
  3. Add the tool to the relevant agent's tool list.
"""

import json
import logging
from typing import Any

from langchain_core.tools import tool

logger = logging.getLogger("legal_assist.tools")


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


# ── Lawyer directory database (Neon Postgres) ───────────────────
@tool
def query_lawyer_database(sql: str) -> str:
    """Execute ONE read-only SELECT query against the lawyer directory
    PostgreSQL database and return the rows as JSON. Only SELECT/WITH
    statements are allowed; the query is validated and capped at 25 rows.
    Use this to filter/count/rank lawyers by city, specialisation,
    experience, fees, rating or reviews."""
    from connectors.neon_postgres import run_select, validate_select_sql  # noqa: PLC0415

    clean_sql, err = validate_select_sql(sql)
    if not clean_sql:
        logger.info("tool query_lawyer_database rejected SQL (%s): %r", err, (sql or "")[:200])
        return _json({"error": f"SQL rejected: {err}", "hint": "Send a single read-only SELECT statement."})
    try:
        result = run_select(clean_sql)
    except Exception as exc:  # noqa: BLE001 — surface DB errors to the model
        logger.warning("tool query_lawyer_database failed: %s", exc)
        return _json({"error": f"Database query failed: {str(exc)[:200]}", "sql": clean_sql})

    logger.info("tool query_lawyer_database executed: %s (%d rows)", clean_sql, result["row_count"])
    return _json({
        "sql": clean_sql,
        "row_count": result["row_count"],
        "columns": result["columns"],
        "rows": result["rows"],
    })


@tool
def list_lawyers() -> str:
    """Return the full lawyer directory (name, specialisation, city,
    experience, fees, rating, chat availability). Use this when the user
    wants to browse or see all available lawyers instead of a filtered list."""
    from connectors.neon_postgres import list_lawyers as _list_lawyers  # noqa: PLC0415

    rows = _list_lawyers()
    if not rows:
        return _json({"error": "Lawyer directory is unreachable right now.", "lawyers": []})
    # Trim heavy fields so the model context stays small
    slim = [
        {k: row.get(k) for k in (
            "id", "name", "specialisation", "city", "state", "experience_years",
            "bar_council_id", "fees_per_hearing", "rating", "reviews_count",
            "available_for_chat", "languages",
        )}
        for row in rows
    ]
    return _json({"lawyers": slim, "count": len(slim)})


# ── Legal dictionary / glossary ─────────────────────────────────
@tool
def define_legal_term(term: str) -> str:
    """Look up the definition of a legal term (e.g. 'habeas corpus',
    'mens rea', 'prima facie') in the legal dictionary. Returns the
    definition or close suggestions."""
    from connectors.legal_dictionary import LegalDictionaryConnector  # noqa: PLC0415

    result = LegalDictionaryConnector().define(term)
    return _json(result)


# ── Bare acts / statutes ────────────────────────────────────────
@tool
def search_bare_acts(act_name: str, section: str = "") -> str:
    """Search Indian bare acts / statutes by act name and optional
    section number (e.g. act_name='Indian Penal Code', section='420').
    Returns the section text when available."""
    from connectors.bare_acts import BareActsConnector  # noqa: PLC0415

    result = BareActsConnector().search(act_name, section)
    return _json(result)


# ── Legal document templates ────────────────────────────────────
@tool
def list_legal_templates() -> str:
    """List the available legal document templates (legal notice, rent
    agreement, consumer complaint, RTI application…) with their ids and
    required fields."""
    from connectors.legal_templates import LegalTemplatesConnector  # noqa: PLC0415

    return _json(LegalTemplatesConnector().list_templates())


@tool
def get_legal_template(template_id: str) -> str:
    """Fetch one legal document template by id (see list_legal_templates
    for valid ids, e.g. 'legal_notice', 'rent_agreement',
    'consumer_complaint', 'rti_application'). Returns the template text
    and its required fields."""
    from connectors.legal_templates import LegalTemplatesConnector  # noqa: PLC0415

    return _json(LegalTemplatesConnector().get_template(template_id))


# ── Tool sets per agent ─────────────────────────────────────────
RESEARCH_TOOLS = [define_legal_term, search_bare_acts]
GENERAL_TOOLS = [define_legal_term]
LAWYER_TOOLS = [list_lawyers, query_lawyer_database]
DB_TOOLS = [query_lawyer_database]
TEMPLATE_TOOLS = [list_legal_templates, get_legal_template]
