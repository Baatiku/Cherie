import base64
import hashlib
import hmac

from fastapi.testclient import TestClient

from omnilive.app import app
from omnilive.ice import turn_rest_credentials


client = TestClient(app)


def test_turn_rest_credentials_are_deterministic():
    username, credential, expires_at = turn_rest_credentials(
        "shared-secret",
        900,
        "device",
        now=1_700_000_000,
    )
    assert username == "1700000900:device"
    assert expires_at == 1_700_000_900
    expected = base64.b64encode(
        hmac.new(b"shared-secret", username.encode(), hashlib.sha1).digest()
    ).decode("ascii")
    assert credential == expected


def test_ice_endpoint_keeps_compatible_server_shape():
    response = client.get("/v1/ice")
    assert response.status_code == 200
    payload = response.json()
    assert payload["servers"]
    assert isinstance(payload["servers"][0]["urls"], list)
    assert payload["credential_mode"] in {"static", "rest_shared_secret"}
