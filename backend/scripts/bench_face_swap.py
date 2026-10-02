"""A/B the swapper engines on an L4 worker with a real photo.

Run from backend/:  python -m modal run scripts/bench_face_swap.py

Same photo as source and target (self-swap): scores identity match
(ArcFace cosine to the source), frame-to-frame flicker on a static
input (pipeline wobble detector), and speed. Enhancer and occluder
are off so the swappers compare fairly.
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

# Different identity from the source: the swap must carry Lena's identity
# onto this face. Self-swap cannot measure transfer strength.
TARGET_CANDIDATES = [
    # Resolved at runtime through the Commons API (no guessed hashes).
    "commons:File:President Barack Obama.jpg",
]

ENGINES = ["inswapper", "hyper1a", "hyper1b", "hyper1c"]


@app.function(
    image=image,
    gpu="L4",
    volumes={MODEL_ROOT: model_volume},
    secrets=[runtime_secret],
    env={
        "OMNILIVE_MODEL_ROOT": MODEL_ROOT,
        "OMNILIVE_ENHANCER_ENABLED": "false",
        "OMNILIVE_XSEG_ENABLED": "true",
    },
    timeout=1800,
)
def bench() -> dict:
    import base64
    import time
    import urllib.parse
    import urllib.request

    import cv2
    import numpy as np

    from omnilive.face_swap import FaceSwapEngine, norm_crop
    from omnilive.models import EffectProfile, QualityProfile
    from omnilive.reference_image import decode_reference_image
    from omnilive.session import registry

    fetch_errors: list[str] = []

    def _fetch(url: str) -> bytes | None:
        UA = {"User-Agent": "OmniLive-bench/0.7 (model evaluation harness)"}

        def _get(link: str, timeout: int):
            request = urllib.request.Request(link, headers=UA)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()

        try:
            if url.startswith("commons:"):
                import json as _api_json

                title = url.split(":", 1)[1]
                api = (
                    "https://commons.wikimedia.org/w/api.php?action=query&titles="
                    + urllib.parse.quote(title)
                    + "&prop=imageinfo&iiprop=url&iiurlwidth=512&format=json"
                )
                payload = _api_json.loads(_get(api, 30).decode("utf-8"))
                pages = payload.get("query", {}).get("pages", {})
                for page in pages.values():
                    infos = page.get("imageinfo") or []
                    if infos and infos[0].get("thumburl"):
                        url = infos[0]["thumburl"]
                        break
                else:
                    fetch_errors.append(url + " -> no thumburl")
                    return None
            data = _get(url, 60)
            if len(data) > 20_000:
                return data
            fetch_errors.append(url + f" -> only {len(data)} bytes")
            return None
        except Exception as exc:
            fetch_errors.append(url + f" -> {type(exc).__name__}: {str(exc)[:120]}")
            return None

    raw = None
    for url in CANDIDATES:
        raw = _fetch(url)
        if raw is not None:
            break
    target_raw = None
    for url in TARGET_CANDIDATES:
        target_raw = _fetch(url)
        if target_raw is not None:
            break
    if raw is None or target_raw is None:
        import json as _json

        failure = {
            "ok": False,
            "detail": "no sample photos downloadable",
            "fetch_errors": fetch_errors,
        }
        print("BENCH-RESULT " + _json.dumps(failure), flush=True)
        return failure

    def _prep(data: bytes) -> np.ndarray:
        photo = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
        photo = cv2.cvtColor(photo, cv2.COLOR_BGR2RGB)
        scale = 540.0 / max(1, photo.shape[0])
        return cv2.resize(
            photo,
            (max(2, int(photo.shape[1] * scale)), max(2, int(photo.shape[0] * scale))),
            interpolation=cv2.INTER_AREA,
        )

    source = _prep(raw)
    target = _prep(target_raw)

    session = registry.create(QualityProfile.BALANCED, EffectProfile.FACE_SWAP, None)
    _, encoded = cv2.imencode(".jpg", cv2.cvtColor(source, cv2.COLOR_RGB2BGR))
    reference = decode_reference_image(base64.b64encode(encoded.tobytes()).decode("ascii"))
    registry.set_reference_image(session.session_id, reference)

    def raw_embedding(detector, arcface, image: np.ndarray):
        faces = [r for r in detector.detect(image) if r.kps is not None]
        if not faces:
            return None
        kps = np.asarray(max(faces, key=lambda r: r.area).kps, dtype=np.float32)
        aligned, _ = norm_crop(image, kps.reshape(5, 2), 112)
        if aligned is None or arcface is None:
            return None
        meta = arcface.get_inputs()[0]
        blob = cv2.dnn.blobFromImage(
            aligned, 1.0 / 127.5, (112, 112), (127.5, 127.5, 127.5), swapRB=True
        )
        vector = np.asarray(arcface.run(None, {meta.name: blob})[0]).reshape(-1)
        return vector / (float(np.linalg.norm(vector)) + 1e-9)

    report: dict = {"ok": True, "photo_shape": list(target.shape), "engines": {}}
    for name in ENGINES:
        engine = FaceSwapEngine(swapper_engine=name)
        # NOTE: every engine uses the same registry session id, because the
        # reference photo is stored per registry session. Each engine keeps
        # its own stream state, so sharing the key is safe.
        key = session.session_id
        try:
            preload = engine.preload()
        except Exception as exc:
            report["engines"][name] = {"ok": False, "detail": str(exc)[:200]}
            continue
        times: list[float] = []
        outputs: list[np.ndarray] = []
        try:
            for _ in range(35):
                started = time.perf_counter()
                out = engine.process_frame(target, key, QualityProfile.BALANCED)
                times.append((time.perf_counter() - started) * 1000.0)
                outputs.append(out)
        except Exception as exc:
            report["engines"][name] = {"ok": False, "detail": str(exc)[:200]}
            continue
        steady = sorted(times[5:])
        flicker = float(
            np.mean(
                [
                    np.abs(outputs[i].astype(np.float32) - outputs[i - 1].astype(np.float32)).mean()
                    for i in range(-10, 0)
                ]
            )
        )
        identity_match = None
        ref_vector = raw_embedding(engine.detector, engine.arcface.load(), source)
        out_vector = raw_embedding(engine.detector, engine.arcface.load(), outputs[-1])
        if ref_vector is not None and out_vector is not None:
            identity_match = round(float(np.dot(ref_vector, out_vector)), 4)
        report["engines"][name] = {
            "ok": True,
            "providers": preload["swapper_model"]["providers"],
            "first_frame_ms": round(times[0], 2),
            "steady_median_ms": round(steady[len(steady) // 2], 2),
            "steady_p95_ms": round(steady[int(len(steady) * 0.95)], 2),
            "flicker": round(flicker, 4),
            "identity_match": identity_match,
        }
        engine.release_stream(key)

    import json
    import os

    bench_dir = os.path.join(MODEL_ROOT, "bench")
    os.makedirs(bench_dir, exist_ok=True)
    with open(os.path.join(bench_dir, "face_swap_bench.json"), "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)
    print("BENCH-RESULT " + json.dumps(report), flush=True)
    return report
