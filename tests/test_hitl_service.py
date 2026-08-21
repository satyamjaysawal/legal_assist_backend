"""Tests for the HITL checkpoint store (in-memory fallback path)."""

import pytest

import services.hitl_service as hitl_service


@pytest.fixture(autouse=True)
def _memory_store(monkeypatch):
    monkeypatch.setattr(hitl_service, "_col", lambda: None)
    hitl_service._FALLBACK.clear()
    yield
    hitl_service._FALLBACK.clear()


def test_save_and_claim_roundtrip():
    hitl_service.save_checkpoint({"request_id": "r1", "user_id": "u1", "query": "q"})
    doc = hitl_service.claim_checkpoint("u1", "r1")
    assert doc is not None
    assert doc["request_id"] == "r1"
    assert doc["query"] == "q"


def test_double_claim_is_rejected():
    hitl_service.save_checkpoint({"request_id": "r1", "user_id": "u1"})
    assert hitl_service.claim_checkpoint("u1", "r1") is not None
    assert hitl_service.claim_checkpoint("u1", "r1") is None


def test_claim_requires_matching_owner():
    hitl_service.save_checkpoint({"request_id": "r1", "user_id": "u1"})
    assert hitl_service.claim_checkpoint("someone-else", "r1") is None


def test_expired_checkpoint_cannot_be_claimed():
    hitl_service.save_checkpoint({
        "request_id": "r1",
        "user_id": "u1",
        "expires_at": "1970-01-01T00:00:00+00:00",
    })
    assert hitl_service.claim_checkpoint("u1", "r1") is None


def test_missing_checkpoint_returns_none():
    assert hitl_service.claim_checkpoint("u1", "nope") is None
