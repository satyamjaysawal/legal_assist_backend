"""Pure tests for explicit POC workflow selection."""

import pytest

from agents.workflow_agent import SPECIALISTS, WORKFLOW_DEFINITIONS, build_workflow_stage_graph, detect_workflow, stream_workflow


@pytest.mark.parametrize("mode", ["sequential", "parallel", "supervisor", "loop", "cycle", "hitl"])
def test_detects_each_explicit_workflow(mode):
    assert detect_workflow(f"workflow: {mode} prepare a legal notice") == mode


def test_workflow_definitions_are_bounded_and_described():
    assert set(WORKFLOW_DEFINITIONS) == {"sequential", "parallel", "supervisor", "loop", "cycle", "hitl"}
    for workflow in WORKFLOW_DEFINITIONS.values():
        assert workflow["agents"]
        assert workflow["pattern"]


def test_hitl_stream_surfaces_each_agent_and_human_checkpoint(monkeypatch):
    def fake_agent(state, config):
        return {"reply": "ok", "agent_metadata": {}}

    for name in tuple(SPECIALISTS):
        monkeypatch.setitem(SPECIALISTS, name, fake_agent)

    events = list(stream_workflow({"messages": [{"role": "user", "content": "workflow: hitl draft"}]}, {}, "hitl"))
    started = [event["agent"] for event in events if event["type"] == "agent_start"]
    done = [event["agent"] for event in events if event["type"] == "agent_done"]

    assert started == ["researcher", "draft", "human_review", "researcher", "draft"]
    assert done == started
    assert any(event["type"] == "workflow" and event["workflow"]["human_in_loop"] for event in events)


def test_each_workflow_specialist_has_a_langgraph_stage():
    graph = build_workflow_stage_graph("researcher")
    assert "researcher" in graph.nodes
