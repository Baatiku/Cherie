from fastapi.testclient import TestClient
from omnilive.app import app

client=TestClient(app)

def test_benchmark_builtin_effect():
    response=client.post("/v1/benchmark",json={
        "effect":"monochrome",
        "width":128,
        "height":96,
        "frames":3,
    })
    assert response.status_code==200
    payload=response.json()
    assert payload["frames"]==3
    assert payload["processor_samples_added"]==3
    assert payload["average_ms"]>=0
