"""Tests for the orchestrator's pure routing logic (no LLM calls)."""

import json

from agents.orchestrator_agent import INTENT_AGENT_MAP, decide_route, parse_analysis


def test_intent_agent_map_covers_all_agents():
    expected_agents = {"assistant", "researcher", "draft", "document_creator",
                       "email", "lawyer_finder", "db_chat", "workflow_supervisor", "case_strategy", "compliance"}
    assert set(INTENT_AGENT_MAP.values()) == expected_agents


def test_parse_analysis_valid_json():
    raw = json.dumps({
        "intent": "db_query",
        "domain": "general",
        "complexity": "simple",
        "route_to": "db_chat",
        "summary": "Find lawyers in Delhi",
        "refined_query": "top lawyers in Delhi",
    })
    analysis = parse_analysis(raw, "find lawyers in delhi")
    assert analysis["route_to"] == "db_chat"
    assert analysis["intent"] == "db_query"
    assert analysis["refined_query"] == "top lawyers in Delhi"


def test_parse_analysis_extracts_json_from_fenced_text():
    raw = 'Here is my analysis:\n```json\n{"intent": "draft", "route_to": "draft"}\n```\nDone.'
    analysis = parse_analysis(raw, "draft a notice")
    assert analysis["route_to"] == "draft"


def test_parse_analysis_garbage_falls_back_to_assistant():
    analysis = parse_analysis("not json at all", "hello there")
    assert analysis["route_to"] == "assistant"
    assert analysis["refined_query"] == "hello there"


def test_parse_analysis_unknown_route_falls_back_via_intent():
    raw = json.dumps({"intent": "email", "route_to": "nonexistent_agent"})
    analysis = parse_analysis(raw, "write an email")
    assert analysis["route_to"] == "email"


def test_parse_analysis_routes_the_extra_specialists():
    for intent, route in (("case_strategy", "case_strategy"), ("compliance", "compliance")):
        analysis = parse_analysis(json.dumps({"intent": intent, "route_to": route}), "help me")
        assert analysis["intent"] == intent
        assert analysis["route_to"] == route


def test_decide_route_valid_routes():
    for agent in ("assistant", "researcher", "draft", "document_creator",
                  "email", "lawyer_finder", "db_chat", "workflow_supervisor", "case_strategy", "compliance"):
        assert decide_route({"routed_to": agent}) == agent


def test_decide_route_invalid_defaults_to_assistant():
    assert decide_route({"routed_to": "hacker_agent"}) == "assistant"
    assert decide_route({}) == "assistant"
    assert decide_route({"routed_to": "  db_chat  "}) == "db_chat"
