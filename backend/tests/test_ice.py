from fastapi.testclient import TestClient
from omnilive.app import app

client = TestClient(app)

def test_ice_config_has_server():
    response = client.get("/v1/ice")
    assert response.status_code == 200
    payload = response.json()
    assert payload["servers"]
    assert payload["servers"][0]["urls"]
