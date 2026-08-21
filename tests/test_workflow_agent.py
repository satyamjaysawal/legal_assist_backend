"""Pure tests for explicit POC workflow selection."""

import pytest

import services.hitl_service as hitl_service
from agents.workflow_agent import (
    SPECIALISTS,
    WORKFLOW_DEFINITIONS,
    build_workflow_stage_graph,
    detect_workflow,
    resume_hitl,
    stream_workflow,
)


@pytest.fixture(autouse=True)
def _memory_checkpoint_store(monkeypatch):
    """Keep HITL tests off real MongoDB — use the in-memory fallback store."""
    monkeypatch.setattr(hitl_service, "_col", lambda: None)
    hitl_service._FALLBACK.clear()
    yield
    hitl_service._FALLBACK.clear()


@pytest.mark.parametrize("mode", ["sequential", "parallel", "supervisor", "loop", "cycle", "hitl"])
def test_detects_each_explicit_workflow(mode):
    assert detect_workflow(f"workflow: {mode} prepare a legal notice") == mode


def test_workflow_definitions_are_bounded_and_described():
    assert set(WORKFLOW_DEFINITIONS) == {"sequential", "parallel", "supervisor", "loop", "cycle", "hitl"}
    for workflow in WORKFLOW_DEFINITIONS.values():
        assert workflow["agents"]
        assert workflow["pattern"]


def test_hitl_stream_pauses_at_checkpoint_with_approval_request(monkeypatch):
    def fake_agent(state, config):
        return {"reply": "ok", "agent_metadata": {}}

    for name in tuple(SPECIALISTS):
        monkeypatch.setitem(SPECIALISTS, name, fake_agent)

    events = list(stream_workflow({"messages": [{"role": "user", "content": "workflow: hitl draft"}]}, {}, "hitl"))
    started = [event["agent"] for event in events if event["type"] == "agent_start"]
    done = [event["agent"] for event in events if event["type"] == "agent_done"]

    # The workflow PAUSES at human_review instead of auto-continuing.
    assert started == ["researcher", "draft", "human_review"]
    assert done == started

    # An interactive approval request is streamed with options + draft.
    hitl_event = next(e for e in events if e["type"] == "hitl")
    assert hitl_event["request_id"]
    assert hitl_event["draft"] == "ok"
    assert {o["id"] for o in hitl_event["options"]} == {"approve", "reject", "changes", "regenerate"}

    # The checkpoint is persisted so a later resume call can claim it.
    assert hitl_service.claim_checkpoint("", hitl_event["request_id"]) is not None

    # Workflow event is marked paused and the stream ends cleanly.
    workflow = next(e["workflow"] for e in events if e["type"] == "workflow")
    assert workflow["paused"] is True and workflow["human_in_loop"] is True
    assert any(e["type"] == "token" and "paused" in e["content"] for e in events)
    assert events[-1]["type"] == "done"


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


# ── HITL resume tests ────────────────────────────────────────

def _make_checkpoint(**overrides):
    checkpoint = {
        "request_id": "req-1",
        "user_id": "u1",
        "journey_id": "j1",
        "query": "workflow: hitl draft a notice",
        "mode": "hitl",
        "counter": 3,
        "nodes": [
            {"stage_id": "researcher:1", "agent": "researcher", "parent_stage_id": "workflow_supervisor:0"},
            {"stage_id": "draft:2", "agent": "draft", "parent_stage_id": "researcher:1"},
            {"stage_id": "human_review:3", "agent": "human_review", "parent_stage_id": "draft:2"},
        ],
        "results": [
            {"agent": "researcher", "reply": "research notes", "metadata": {}, "input": ""},
            {"agent": "draft", "reply": "first draft", "metadata": {}, "input": "research notes"},
            {"agent": "human_review", "reply": "checkpoint", "metadata": {"human_in_loop": True}, "input": "first draft"},
        ],
        "draft_reply": "first draft",
        "checkpoint_stage_id": "human_review:3",
        "state_extras": {},
    }
    checkpoint.update(overrides)
    return checkpoint


def _stub_specialists(monkeypatch):
    def fake_agent(state, config):
        return {"reply": "stage output", "agent_metadata": {}}

    for name in tuple(SPECIALISTS):
        monkeypatch.setitem(SPECIALISTS, name, fake_agent)


def test_resume_hitl_approve_runs_review_and_final_draft(monkeypatch):
    _stub_specialists(monkeypatch)
    events = list(resume_hitl(_make_checkpoint(), "approve", "looks good"))
    started = [e["agent"] for e in events if e["type"] == "agent_start"]
    assert started == ["human_decision", "researcher", "draft"]
    decision_done = next(e for e in events if e["type"] == "agent_done" and e["agent"] == "human_decision")
    assert "Approved" in decision_done["reply"] and "looks good" in decision_done["reply"]
    workflow = next(e["workflow"] for e in events if e["type"] == "workflow")
    assert workflow["paused"] is False and workflow["decision"] == "approve"
    assert any(e["type"] == "token" and e["content"] for e in events)
    assert events[-1]["type"] == "done"


def test_resume_hitl_reject_ends_workflow_without_new_stages(monkeypatch):
    _stub_specialists(monkeypatch)
    events = list(resume_hitl(_make_checkpoint(), "reject", "wrong jurisdiction"))
    started = [e["agent"] for e in events if e["type"] == "agent_start"]
    assert started == ["human_decision"]
    workflow = next(e["workflow"] for e in events if e["type"] == "workflow")
    assert workflow["decision"] == "reject"
    assert any(e["type"] == "token" for e in events)


def test_resume_hitl_regenerate_pauses_again_at_fresh_checkpoint(monkeypatch):
    _stub_specialists(monkeypatch)
    events = list(resume_hitl(_make_checkpoint(), "regenerate", "make it shorter"))
    started = [e["agent"] for e in events if e["type"] == "agent_start"]
    assert started == ["human_decision", "draft", "human_review"]
    hitl_event = next(e for e in events if e["type"] == "hitl")
    assert hitl_event["request_id"] != "req-1"  # fresh checkpoint
    assert hitl_service.claim_checkpoint("", hitl_event["request_id"]) is not None
    workflow = next(e["workflow"] for e in events if e["type"] == "workflow")
    assert workflow["paused"] is True


def test_resume_hitl_unknown_decision_yields_error():
    events = list(resume_hitl(_make_checkpoint(), "maybe", ""))
    assert events == [{"type": "error", "detail": "Unknown HITL decision: maybe"}]
