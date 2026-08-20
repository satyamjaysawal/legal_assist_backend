"""Local smoke test: /lawyers directory + lawyer chat room + WebSocket demo reply."""
from fastapi.testclient import TestClient

import main as app_module

client = TestClient(app_module.app)

r = client.post("/auth/login", json={"email": "demotest@sunny.dev", "password": "Demo@12345"})
print("login:", r.status_code)
assert r.status_code == 200, r.text
token = r.json()["token"]
user_id = r.json()["user"]["user_id"]
h = {"Authorization": f"Bearer {token}"}

r = client.get("/lawyers", headers=h)
print("lawyers:", r.status_code, "count =", r.json().get("count") if r.status_code == 200 else r.text[:200])
assert r.status_code == 200
lawyers = r.json()["lawyers"]
lawyer = next(l for l in lawyers if l["available_for_chat"])
print("picked:", lawyer["name"], "|", lawyer["specialisation"], "|", lawyer["city"])

r = client.post(
    "/lawyer/rooms",
    headers=h,
    json={
        "lawyer_id": str(lawyer["id"]),
        "lawyer_name": lawyer["name"],
        "lawyer_meta": f"{lawyer['specialisation']} · {lawyer['city']}",
    },
)
print("room:", r.status_code, r.json())
assert r.status_code == 200
room_id = r.json()["room_id"]

r = client.get("/lawyer/rooms", headers=h)
print("rooms list:", r.status_code, len(r.json()["rooms"]), "room(s)")

with client.websocket_connect(f"/ws/lawyer/user/{room_id}?user_id={user_id}") as ws:
    evt = ws.receive_json()
    print("connected evt:", evt["type"], "| lawyer_name:", evt.get("lawyer_name"))
    assert evt["type"] == "connected"
    ws.send_json({"type": "message", "text": "Hi, I need help with a property dispute."})
    ack = ws.receive_json()
    print("sent ack:", ack["type"])
    reply = ws.receive_json()
    print("lawyer reply:", reply["type"], "| simulated:", reply.get("simulated"))
    print("  text:", reply["text"][:150])
    ws.send_json({"type": "message", "text": "It started last month with a notice."})
    ws.receive_json()  # sent ack
    reply2 = ws.receive_json()
    print("second reply:", reply2["text"][:120])
    ws.send_json({"type": "end_session"})
    end = ws.receive_json()
    print("end:", end["type"])

r = client.delete(f"/lawyer/rooms/{room_id}", headers=h)
print("close room:", r.status_code, r.json())
print("ALL OK")
