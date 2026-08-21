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


def test_stage_events_carry_input_reply_and_parent_chain(monkeypatch):
    def fake_agent(state, config):
        return {"reply": "stage output", "agent_metadata": {}}

    for name in tuple(SPECIALISTS):
        monkeypatch.setitem(SPECIALISTS, name, fake_agent)

    events = list(stream_workflow({"messages": [{"role": "user", "content": "workflow: hitl draft"}]}, {}, "hitl"))
    starts = {e["stage_id"]: e for e in events if e["type"] == "agent_start"}
    dones = {e["stage_id"]: e for e in events if e["type"] == "agent_done"}

    # Every start has a matching done with the same stage id.
    assert set(starts) == set(dones)
    # The supervisor root is the parent of the first researcher stage.
    assert starts["researcher:1"]["parent_stage_id"] == "workflow_supervisor:0"
    # The draft receives the researcher reply as its hand-off input.
    assert "stage output" in starts["draft:2"]["input"]
    assert starts["draft:2"]["parent_stage_id"] == "researcher:1"
    # Done events expose the stage reply for the UI tree.
    assert dones["researcher:1"]["reply"] == "stage output"
    # The workflow event carries the node list for tree rendering.
    workflow = next(e["workflow"] for e in events if e["type"] == "workflow")
    assert {"stage_id": "draft:2", "agent": "draft", "parent_stage_id": "researcher:1"} in workflow["nodes"]


def test_failed_stage_does_not_kill_the_workflow(monkeypatch):
    def boom(state, config):
        raise RuntimeError("model unavailable")

    def fake_agent(state, config):
        return {"reply": "ok", "agent_metadata": {}}

    monkeypatch.setitem(SPECIALISTS, "researcher", boom)
    monkeypatch.setitem(SPECIALISTS, "draft", fake_agent)
    monkeypatch.setitem(SPECIALISTS, "assistant", fake_agent)

    events = list(stream_workflow({"messages": [{"role": "user", "content": "workflow: sequential draft"}]}, {}, "sequential"))
    done = {e["stage_id"]: e for e in events if e["type"] == "agent_done"}

    assert done["researcher:1"]["failed"] is True
    assert "could not complete" in done["researcher:1"]["reply"]
    # The workflow still finishes with a rendered reply instead of dying.
    assert any(e["type"] == "token" and e["content"] for e in events)
    assert any(e["type"] == "done" for e in events)


def test_each_workflow_specialist_has_a_langgraph_stage():
    graph = build_workflow_stage_graph("researcher")
    assert "researcher" in graph.nodes
