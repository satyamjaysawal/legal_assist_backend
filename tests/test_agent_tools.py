"""Tests for the @tool wrappers (agents/agent_tools.py).

Local connectors (dictionary, templates) run without network.
The Postgres-backed tools are tested with a mocked run_select.
"""

import json

from agents.agent_tools import (
    DB_TOOLS,
    GENERAL_TOOLS,
    LAWYER_TOOLS,
    RESEARCH_TOOLS,
    TEMPLATE_TOOLS,
    define_legal_term,
    get_legal_template,
    list_legal_templates,
    list_lawyers,
    query_lawyer_database,
    search_bare_acts,
)


# ── Tool sets ───────────────────────────────────────────────────
def test_tool_sets_are_consistent():
    assert GENERAL_TOOLS == [define_legal_term]
    assert RESEARCH_TOOLS == [define_legal_term, search_bare_acts]
    assert LAWYER_TOOLS == [list_lawyers, query_lawyer_database]
    assert DB_TOOLS == [query_lawyer_database]
    assert TEMPLATE_TOOLS == [list_legal_templates, get_legal_template]


def test_tools_have_langchain_metadata():
    for t in GENERAL_TOOLS + RESEARCH_TOOLS + LAWYER_TOOLS + TEMPLATE_TOOLS:
        assert t.name
        assert t.description  # docstrings become the tool description


# ── Legal dictionary tool ───────────────────────────────────────
def test_define_legal_term_known_term():
    data = json.loads(define_legal_term.invoke({"term": "habeas corpus"}))
    assert data["available"] is True
    assert data["definition"]


def test_define_legal_term_unknown_term_gives_suggestions():
    data = json.loads(define_legal_term.invoke({"term": "zzz-not-a-term"}))
    assert data["definition"] is None
    assert "suggestions" in data


# ── Bare acts tool ──────────────────────────────────────────────
def test_search_bare_acts_returns_results():
    data = json.loads(search_bare_acts.invoke({"act_name": "Indian Penal Code", "section": "420"}))
    assert data["connector"] == "bare_acts"
    assert isinstance(data["results"], list)


# ── Templates tools ─────────────────────────────────────────────
def test_list_legal_templates():
    data = json.loads(list_legal_templates.invoke({}))
    assert data["available"] is True
    assert len(data["templates"]) >= 4


def test_get_legal_template_valid_id():
    data = json.loads(get_legal_template.invoke({"template_id": "legal_notice"}))
    assert data.get("error") is None or data.get("available")
    assert "template" in data


def test_get_legal_template_invalid_id():
    data = json.loads(get_legal_template.invoke({"template_id": "nope"}))
    assert data.get("error")


# ── Lawyer database tool (mocked DB) ────────────────────────────
def test_query_lawyer_database_rejects_write_sql_without_db(monkeypatch):
    import connectors.neon_postgres as pg

    def _explode(sql, limit=25):  # pragma: no cover — must never be reached
        raise AssertionError("run_select must not be called for rejected SQL")

    monkeypatch.setattr(pg, "run_select", _explode)
    data = json.loads(query_lawyer_database.invoke({"sql": "DELETE FROM lawyers"}))
    assert data["error"].startswith("SQL rejected")


def test_query_lawyer_database_returns_rows(monkeypatch):
    import connectors.neon_postgres as pg

    fake = {
        "sql": "SELECT id, name FROM lawyers LIMIT 3",
        "row_count": 2,
        "columns": ["id", "name"],
        "rows": [{"id": 1, "name": "A. Lawyer"}, {"id": 2, "name": "B. Advocate"}],
    }
    monkeypatch.setattr(pg, "run_select", lambda sql, limit=25: fake)
    data = json.loads(query_lawyer_database.invoke({"sql": "SELECT id, name FROM lawyers LIMIT 3"}))
    assert data["row_count"] == 2
    assert data["columns"] == ["id", "name"]
    assert len(data["rows"]) == 2


def test_query_lawyer_database_db_error_surfaced(monkeypatch):
    import connectors.neon_postgres as pg

    def _fail(sql, limit=25):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(pg, "run_select", _fail)
    data = json.loads(query_lawyer_database.invoke({"sql": "SELECT 1 LIMIT 1"}))
    assert "error" in data
    assert "sql" in data


def test_list_lawyers_unreachable_returns_error_json(monkeypatch):
    import connectors.neon_postgres as pg

    monkeypatch.setattr(pg, "list_lawyers", lambda available_only=False: [])
    data = json.loads(list_lawyers.invoke({}))
    assert data["error"]
    assert data["lawyers"] == []
