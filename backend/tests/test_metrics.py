from fastapi.testclient import TestClient

from omnilive.app import app

client = TestClient(app)


def test_metrics_endpoint_exposes_core_counters():
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "omnilive_processor_latency_ms" in response.text
    assert "omnilive_input_overwritten_frames_total" in response.text
    assert "omnilive_model_loaded" in response.text


def test_health_has_explicit_status():
    response = client.get("/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] in {"ok", "model_unavailable"}
    assert isinstance(payload["ok"], bool)
