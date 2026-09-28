from fastapi.testclient import TestClient

from omnilive.app import app


client = TestClient(app)


def test_live_websocket_rejects_non_start_first_message():
    with client.websocket_connect("/v1/live") as websocket:
        websocket.send_json({"action": "ping"})
        response = websocket.receive_json()
        assert response["event"] == "error"
        assert "start" in response["detail"]


def test_live_websocket_rejects_invalid_start_payload():
    with client.websocket_connect("/v1/live") as websocket:
        websocket.send_json({"action": "start", "profile": "balanced"})
        response = websocket.receive_json()
        assert response["event"] == "error"
