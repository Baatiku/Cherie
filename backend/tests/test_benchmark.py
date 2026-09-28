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


def test_benchmark_matrix_uses_live_processor_path():
    response=client.post("/v1/benchmark/matrix",json={
        "effect":"monochrome",
        "profiles":["low_latency"],
        "crop_sizes":[64,96],
        "width":128,
        "height":128,
        "warmup_frames":0,
        "steady_frames":3,
        "force_region":True,
    })
    assert response.status_code==200
    payload=response.json()
    assert payload["case_count"]==2
    assert payload["crop_sizes"]==[64,96]
    assert len(payload["cases"])==2
    for case in payload["cases"]:
        assert case["steady_frames"]==3
        assert case["steady_pipeline_p95_ms"]>=0
        assert case["requested_crop_max_side"] in (64,96)
        assert case["inference_attempts"]==4
