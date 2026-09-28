from fastapi.testclient import TestClient
from omnilive.app import app

client = TestClient(app)

def test_offer_requires_valid_session():
    response = client.post("/v1/webrtc/offer", json={
        "session_id": "missing-session",
        "token": "not-a-valid-token",
        "sdp": "v=0\r\n",
        "type": "offer",
    })
    assert response.status_code == 401
