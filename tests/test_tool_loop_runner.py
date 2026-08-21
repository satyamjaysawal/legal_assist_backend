"""Tests for the agentic tool-loop runner (agents/tool_loop_runner.py).

The full loop is tested with a scripted fake LLM — no network calls.
"""

import json

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool

from agents import tool_loop_runner
from agents.tool_loop_runner import (
    _extract_payload,
    _has_tool_calls,
    _strip_think,
    _tool_trace,
    run_agent_with_tools,
)


# ── Pure helpers ────────────────────────────────────────────────
def test_strip_think_removes_reasoning_blocks():
    text = "<think>reasoning here</think>The answer is 42."
    assert _strip_think(text) == "The answer is 42."


def test_has_tool_calls():
    assert _has_tool_calls(AIMessage(content="", tool_calls=[
        {"name": "t", "args": {}, "id": "1"}
    ])) is True
    assert _has_tool_calls(AIMessage(content="plain")) is False


def test_tool_trace_marks_success_and_errors():
    messages = [
        ToolMessage(content=json.dumps({"rows": []}), name="query_lawyer_database", tool_call_id="c1"),
        ToolMessage(content=json.dumps({"error": "boom"}), name="query_lawyer_database", tool_call_id="c2"),
    ]
    trace = _tool_trace(messages)
    assert trace == [
        {"tool": "query_lawyer_database", "ok": True},
        {"tool": "query_lawyer_database", "ok": False},
    ]


def test_extract_payload_takes_last_successful_call():
    messages = [
        ToolMessage(content=json.dumps({"sql": "SELECT 1", "row_count": 0}), name="query_lawyer_database", tool_call_id="c1"),
        ToolMessage(content=json.dumps({"error": "rejected"}), name="query_lawyer_database", tool_call_id="c2"),
        ToolMessage(content=json.dumps({"sql": "SELECT 2", "row_count": 1}), name="query_lawyer_database", tool_call_id="c3"),
        ToolMessage(content=json.dumps({"term": "x"}), name="define_legal_term", tool_call_id="c4"),
    ]
    payload = _extract_payload(messages, "query_lawyer_database")
    assert payload["sql"] == "SELECT 2"
    assert _extract_payload(messages, "nonexistent_tool") is None


def test_extract_payload_ignores_non_json_content():
    messages = [ToolMessage(content="not json", name="query_lawyer_database", tool_call_id="c1")]
    assert _extract_payload(messages, "query_lawyer_database") is None


# ── Full loop with a scripted fake LLM ──────────────────────────
@tool
def add_two(x: int) -> str:
    """Add 2 to the given number."""
    return json.dumps({"result": int(x) + 2})


class _FakeToolCallingLLM:
    """Scripted LLM: first turn calls a tool, second turn answers."""

    def __init__(self):
        self.calls = 0

    def bind_tools(self, tools):
        return self

    def invoke(self, messages, config=None):
        self.calls += 1
        if self.calls == 1:
            return AIMessage(content="", tool_calls=[
                {"name": "add_two", "args": {"x": 40}, "id": "call_1", "type": "tool_call"},
            ])
        return AIMessage(content="The answer is 42.")


def test_run_agent_with_tools_full_loop(monkeypatch):
    monkeypatch.setattr(tool_loop_runner, "get_llm", lambda *a, **k: _FakeToolCallingLLM())

    result = run_agent_with_tools(
        "You are a test agent.",
        [{"role": "user", "content": "what is 40 + 2?"}],
        [add_two],
        {"configurable": {"api_key": "dummy", "model": "fake/model"}},
    )

    assert result["agentic"] is True
    assert result["reply"] == "The answer is 42."
    assert result["tool_trace"] == [{"tool": "add_two", "ok": True}]
    assert result["tool_payloads"]["add_two"] == {"result": 42}


class _NoToolSupportLLM:
    """LLM whose bind_tools fails — forces graceful degradation."""

    def bind_tools(self, tools):
        raise NotImplementedError("tools not supported")

    def invoke(self, messages, config=None):
        return AIMessage(content="Plain fallback answer.")


def test_run_agent_with_tools_degrades_when_bind_fails(monkeypatch):
    monkeypatch.setattr(tool_loop_runner, "get_llm", lambda *a, **k: _NoToolSupportLLM())

    result = run_agent_with_tools(
        "You are a test agent.",
        [{"role": "user", "content": "hello"}],
        [add_two],
        {"configurable": {"api_key": "dummy", "model": "fake/model"}},
    )

    assert result["agentic"] is False
    assert result["reply"] == "Plain fallback answer."
    assert result["tool_trace"] == []
    assert result["tool_payloads"] == {}
