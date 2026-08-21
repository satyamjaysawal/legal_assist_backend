"""Follow-up questions must only appear after the chat is complete.

A paused HITL workflow is NOT complete — the approval card is the pending
interaction — so the paused stream must not generate or emit follow-ups.
The resume stream emits them only when the workflow truly finishes
(regenerate re-pauses → still no follow-ups).
"""

import json

import pytest


@pytest.fixture()
def authed(client):
    import main
    from services.auth_service import current_user

    main.app.dependency_overrides[current_user] = lambda: {
        "user_id": "u-followup",
        "email": "followup@test.local",
        "name": "Followup",
        "role": "user",
    }
    yield
    main.app.dependency_overrides.pop(current_user, None)


def _patch_pipeline(monkeypatch, events):
    import main

    calls = {"followups": 0}

    monkeypatch.setattr(main, "require_key", lambda: "test-key")
    monkeypatch.setattr(main, "resolve_journey", lambda user, jid: {"journey_id": "j-followup"})
    monkeypatch.setattr(
        main,
        "load_all",
        lambda *a, **k: {
            "history": [],
            "facts": [],
            "notes": "",
            "episodic_notes": "",
            "procedural_notes": "",
            "layers": [],
        },
    )
    monkeypatch.setattr(main, "load_user_profile", lambda uid: ("", {}))
    monkeypatch.setattr(main, "search_docs", lambda *a, **k: ([], {"status": "skip"}))
    monkeypatch.setattr(main, "stream_multi_graph", lambda *a, **k: iter(events))
    monkeypatch.setattr(main, "suggest_title", lambda *a, **k: "title")
    monkeypatch.setattr(main, "save_all", lambda *a, **k: [])

    def spy_followups(*a, **k):
        calls["followups"] += 1
        return ["next question one?", "next question two?"]

    monkeypatch.setattr(main, "suggest_followups", spy_followups)
    return calls


def _sse_types(res) -> list[str]:
    types = []
    for line in res.iter_lines():
        if line.startswith("data: "):
            types.append(json.loads(line[len("data: "):]).get("type"))
    return types


def test_paused_workflow_emits_no_followups(client, authed, monkeypatch):
    events = [
        {"type": "hitl", "request_id": "r-1", "stage_id": "human_review:3",
         "question": "Approve?", "draft": "draft", "options": []},
        {"type": "workflow", "workflow": {"paused": True}},
        {"type": "token", "content": "⏸️ paused"},
        {"type": "done"},
    ]
    calls = _patch_pipeline(monkeypatch, events)

    res = client.post(
        "/chat/stream/v2",
        json={"messages": [{"role": "user", "content": "workflow: hitl draft a consumer complaint for a defective laptop refund"}]},
    )
    assert res.status_code == 200
    types = _sse_types(res)
    assert "hitl" in types
    assert "followups" not in types, "follow-ups must not appear before the human decides"
    assert calls["followups"] == 0, "no LLM follow-up call while paused"


def test_completed_workflow_still_emits_followups(client, authed, monkeypatch):
    events = [
        {"type": "agent_route", "routed_to": "assistant", "analysis": {}, "metadata": {}},
        {"type": "token", "content": "a complete answer about limitation periods"},
        {"type": "done"},
    ]
    calls = _patch_pipeline(monkeypatch, events)

    res = client.post(
        "/chat/stream/v2",
        json={"messages": [{"role": "user", "content": "limitation period for filing a consumer complaint in india"}]},
    )
    assert res.status_code == 200
    types = _sse_types(res)
    assert "followups" in types
    assert calls["followups"] == 1


def _patch_resume(monkeypatch, events, query="workflow: hitl draft"):
    import main

    calls = {"followups": 0}

    monkeypatch.setattr(main, "require_key", lambda: "test-key")
    monkeypatch.setattr(
        main,
        "claim_checkpoint",
        lambda user_id, request_id: {"request_id": request_id, "user_id": user_id,
                                     "journey_id": "j-resume", "query": query},
    )
    monkeypatch.setattr(main, "resume_hitl", lambda *a, **k: iter(events))
    monkeypatch.setattr(main, "get_journey", lambda *a, **k: {"messages": [], "title": "t"})
    monkeypatch.setattr(main, "save_all", lambda *a, **k: [])

    def spy_followups(*a, **k):
        calls["followups"] += 1
        return ["after completion question?"]

    monkeypatch.setattr(main, "suggest_followups", spy_followups)
    return calls


def test_resume_completion_emits_followups(client, authed, monkeypatch):
    events = [
        {"type": "workflow", "workflow": {"paused": False, "decision": "approve"}},
        {"type": "token", "content": "final approved draft"},
        {"type": "done"},
    ]
    calls = _patch_resume(monkeypatch, events)

    res = client.post("/chat/hitl/resume", json={"request_id": "r-9", "decision": "approve"})
    assert res.status_code == 200
    types = _sse_types(res)
    assert "followups" in types, "follow-ups belong at true completion (after the decision)"
    assert calls["followups"] == 1


def test_resume_repause_emits_no_followups(client, authed, monkeypatch):
    events = [
        {"type": "token", "content": "regenerated draft"},
        {"type": "hitl", "request_id": "r-10", "stage_id": "human_review:5",
         "question": "Approve?", "draft": "new", "options": []},
        {"type": "done"},
    ]
    calls = _patch_resume(monkeypatch, events)

    res = client.post("/chat/hitl/resume", json={"request_id": "r-9", "decision": "regenerate"})
    assert res.status_code == 200
    types = _sse_types(res)
    assert "followups" not in types, "regenerate re-pauses — still not complete"
    assert calls["followups"] == 0
