"""Tests for the agent registry and LangGraph wiring."""

import os

import agents  # noqa: F401 — importing the package registers most agents
import agents.email_agent  # noqa: F401 — registered via the graph module normally
from agents.base import AGENT_REGISTRY, get_agent, list_agents


EXPECTED_AGENTS = {
    "orchestrator",
    "assistant",
    "researcher",
    "draft",
    "document_creator",
    "email",
    "lawyer_finder",
    "db_chat",
    "workflow_supervisor",
    "case_strategy",
    "compliance",
    "negotiation",
    "risk_assessment",
}


def test_all_agents_registered():
    assert EXPECTED_AGENTS.issubset(set(AGENT_REGISTRY.keys()))


def test_list_agents_returns_metadata():
    names = [a["name"] for a in list_agents()]
    assert EXPECTED_AGENTS.issubset(set(names))
    for entry in list_agents():
        assert entry.get("handles")
        assert entry.get("description")


def test_get_agent_returns_metadata_dict():
    for name in EXPECTED_AGENTS:
        agent = get_agent(name)
        assert agent is not None
        assert isinstance(agent["handles"], list)
        assert agent["handles"]


def test_get_agent_unknown_returns_none():
    assert get_agent("does_not_exist") is None


def test_multi_agent_graph_compiles_with_all_nodes():
    from agents.multi_agent_graph import build_multi_graph

    graph = build_multi_graph(os.getenv("GROQ_API_KEY", "dummy"), "openai/gpt-oss-20b")
    nodes = set(graph.nodes.keys())
    assert EXPECTED_AGENTS.issubset(nodes)
