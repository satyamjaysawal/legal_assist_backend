"""Anonymous no-login full-access account tests (in-memory Mongo fake)."""

import services.auth_service as auth_service


class FakeCol:
    def __init__(self):
        self.docs = []

    def create_index(self, *args, **kwargs):
        return None

    def insert_one(self, doc):
        self.docs.append(doc)
        return doc

    def find_one(self, query):
        for doc in self.docs:
            if all(doc.get(key) == value for key, value in query.items()):
                return doc
        return None

    def find(self, query):
        matches = [d for d in self.docs if all(d.get(k) == v for k, v in query.items())]

        class Cursor(list):
            def sort(self, *args, **kwargs):
                return self

        return Cursor(matches)


class FakeDB(dict):
    def __getitem__(self, name):
        return self.setdefault(name, FakeCol())


class FakeClient(dict):
    def __getitem__(self, name):
        return self.setdefault(name, FakeDB())


def test_create_anonymous_user_is_full_access_role_user(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(auth_service, "get_mongo", lambda: fake)

    user = auth_service.create_anonymous_user()

    assert user["role"] == "user"  # same privileges as a signed-up user
    doc = fake[auth_service.MONGO_DB]["users"].docs[0]
    assert doc["anonymous"] is True
    assert doc["email"].startswith("anon-")
    assert doc["password_hash"]  # unusable random hash, token-only access
    # public payload must not leak the hash
    assert "password_hash" not in user


def test_anonymous_endpoint_returns_token_and_journey(client, monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(auth_service, "get_mongo", lambda: fake)
    # journey_service also resolves its collection through get_mongo
    import services.journey_service as journey_service

    monkeypatch.setattr(journey_service, "get_mongo", lambda: fake)

    res = client.post("/auth/anonymous")
    assert res.status_code == 200
    data = res.json()
    assert data["token"]
    assert data["anonymous"] is True
    assert data["user"]["role"] == "user"
    assert data["journey"]["journey_id"]

    # The issued token authenticates against protected endpoints.
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {data['token']}"})
    assert me.status_code == 200
    assert me.json()["user"]["role"] == "user"
