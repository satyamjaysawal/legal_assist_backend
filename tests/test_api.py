"""API-level tests for public endpoints (no auth required)."""


def test_health_endpoint(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["stack"]["multi_agent"] is True
    agent_names = {a["name"] for a in body["agents"]}
    assert {"orchestrator", "assistant", "researcher", "db_chat"}.issubset(agent_names)


def test_cors_allows_only_the_canonical_production_frontend(client):
    allowed_origin = "https://legal-assist-agentic.vercel.app"
    allowed = client.options(
        "/health",
        headers={
            "Origin": allowed_origin,
            "Access-Control-Request-Method": "POST",
        },
    )
    blocked = client.options(
        "/health",
        headers={
            "Origin": "https://untrusted-preview.vercel.app",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == allowed_origin
    assert blocked.status_code == 400
    assert "access-control-allow-origin" not in blocked.headers


def test_connectors_endpoint(client):
    resp = client.get("/connectors")
    assert resp.status_code == 200
    names = {c["name"] for c in resp.json()["connectors"]}
    assert {"bare_acts", "legal_dictionary", "legal_templates", "neon_postgres"}.issubset(names)


def test_single_connector_endpoint(client):
    resp = client.get("/connectors/legal_dictionary")
    assert resp.status_code == 200


def test_unknown_connector_returns_404(client):
    resp = client.get("/connectors/does_not_exist")
    assert resp.status_code == 404


def test_protected_endpoint_requires_auth(client):
    resp = client.get("/journeys")
    assert resp.status_code == 401


def test_registration_cannot_select_a_privileged_role(client, monkeypatch):
    """Public signup must always create a standard user account."""
    import main

    captured = {}

    def fake_register(email, password, name, role):
        captured["role"] = role
        return {"user_id": "user-1", "email": email, "name": name, "role": role}

    monkeypatch.setattr(main, "register_user", fake_register)
    monkeypatch.setattr(main, "make_token", lambda *_: "test-token")
    monkeypatch.setattr(main, "create_journey", lambda *_: {"journey_id": "journey-1"})

    resp = client.post(
        "/auth/register",
        json={"email": "new@example.com", "password": "secure-password", "role": "admin"},
    )

    assert resp.status_code == 200
    assert captured["role"] == "user"
    assert resp.json()["user"]["role"] == "user"
