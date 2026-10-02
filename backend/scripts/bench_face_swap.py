"""Benchmark the live face-swap path on an L4 worker with a real photo.

Run from backend/:  python -m modal run scripts/bench_face_swap.py

Downloads a sample face photo, registers it as the session reference
(exactly like the Android upload path), then times full-frame swaps.
"""

from __future__ import annotations

import modal

MODEL_ROOT = "/models"
model_volume = modal.Volume.from_name("omnilive-models", create_if_missing=True)
runtime_secret = modal.Secret.from_name("omnilive-runtime")

image = (
    modal.Image.from_registry(
        "nvidia/cuda:12.6.3-cudnn-runtime-ubuntu22.04", add_python="3.12"
    )
    .apt_install("libgl1", "libglib2.0-0")
    .pip_install(
        "pydantic>=2.8,<3",
        "numpy>=2,<3",
        "opencv-python-headless>=4.10,<5",
        "onnx>=1.17,<2",
        "onnxruntime-gpu==1.22.0",
    )
    .add_local_python_source("omnilive")
)

app = modal.App("omnilive-swap-bench")

CANDIDATES = [
    "https://raw.githubusercontent.com/opencv/opencv/master/samples/data/lena.jpg",
    "https://upload.wikimedia.org/wikipedia/en/7/7d/Lenna_%28test_image%29.png",
]


@app.function(
    image=image,
    gpu="L4",
    volumes={MODEL_ROOT: model_volume},
    secrets=[runtime_secret],
    env={"OMNILIVE_MODEL_ROOT": MODEL_ROOT},
    timeout=1200,
)
def bench() -> dict:
    import base64
    import time
    import urllib.request

    import cv2
    import numpy as np

    from omnilive.face_swap import engine
    from omnilive.models import EffectProfile, QualityProfile
    from omnilive.reference_image import decode_reference_image
    from omnilive.session import registry

    raw = None
    for url in CANDIDATES:
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                data = response.read()
            if len(data) > 50_000:
                raw = data
                break
        except Exception:
            continue
    if raw is None:
        import json as _json
        import os as _os

        failure = {"ok": False, "detail": "no sample photo downloadable"}
        _os.makedirs(_os.path.join(MODEL_ROOT, "bench"), exist_ok=True)
        with open(_os.path.join(MODEL_ROOT, "bench", "face_swap_bench.json"), "w", encoding="utf-8") as handle:
            _json.dump(failure, handle, indent=2)
        print("BENCH-RESULT " + _json.dumps(failure), flush=True)
        return failure

    photo = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
    photo = cv2.cvtColor(photo, cv2.COLOR_BGR2RGB)
    scale = 540.0 / max(1, photo.shape[0])
    target = cv2.resize(
        photo,
        (max(2, int(photo.shape[1] * scale)), max(2, int(photo.shape[0] * scale))),
        interpolation=cv2.INTER_AREA,
    )

    preload = engine.preload()
    session = registry.create(QualityProfile.BALANCED, EffectProfile.FACE_SWAP, None)
    _, encoded = cv2.imencode(".jpg", cv2.cvtColor(target, cv2.COLOR_RGB2BGR))
    reference = decode_reference_image(base64.b64encode(encoded.tobytes()).decode("ascii"))
    registry.set_reference_image(session.session_id, reference)

    times: list[float] = []
    diffs: list[float] = []
    for _ in range(30):
        started = time.perf_counter()
        out = engine.process_frame(target, session.session_id, QualityProfile.BALANCED)
        times.append((time.perf_counter() - started) * 1000.0)
        diffs.append(float(np.abs(out.astype(np.float32) - target.astype(np.float32)).mean()))
    ordered = sorted(times)
    result = {
        "ok": True,
        "photo_shape": list(target.shape),
        "providers": preload["swapper_model"]["providers"],
        "arcface_providers": preload["arcface_model"]["providers"],
        "emap_loaded": preload["emap_loaded"],
        "source_ready": preload["face_swap_source_ready"],
        "first_frame_ms": round(times[0], 2),
        "steady_median_ms": round(ordered[len(ordered) // 2], 2),
        "steady_p95_ms": round(ordered[int(len(ordered) * 0.95)], 2),
        "mean_pixel_change": round(sum(diffs[5:]) / max(1, len(diffs[5:])), 3),
        "swap_status": engine.status(),
    }
    import json
    import os

    bench_dir = os.path.join(MODEL_ROOT, "bench")
    os.makedirs(bench_dir, exist_ok=True)
    with open(os.path.join(bench_dir, "face_swap_bench.json"), "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2)
    print("BENCH-RESULT " + json.dumps(result), flush=True)
    return result
