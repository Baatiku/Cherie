"""Probe SCRFD landmark positions and HyperSwap output on open vs closed mouth.

Run from backend/:  python -m modal run scripts/probe_scrfd.py
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

app = modal.App("omnilive-scrfd-probe")


@app.function(
    image=image,
    gpu="L4",
    volumes={MODEL_ROOT: model_volume},
    secrets=[runtime_secret],
    env={"OMNILIVE_MODEL_ROOT": MODEL_ROOT},
    timeout=600,
)
def probe_kps() -> dict:
    import json
    import urllib.parse
    import urllib.request
    import cv2
    import numpy as np

    from omnilive.vision import ScrfdFaceDetector, resolve_vision_model_path
    from omnilive.config import settings

    UA = {"User-Agent": "OmniLive-probe/0.7"}

    def _fetch_commons(title: str) -> np.ndarray:
        api = (
            "https://commons.wikimedia.org/w/api.php?action=query&titles="
            + urllib.parse.quote(title)
            + "&prop=imageinfo&iiprop=url&iiurlwidth=512&format=json"
        )
        req = urllib.request.Request(api, headers=UA)
        with urllib.request.urlopen(req, timeout=30) as r:
            payload = json.loads(r.read().decode("utf-8"))
        pages = payload.get("query", {}).get("pages", {})
        url = None
        for page in pages.values():
            infos = page.get("imageinfo") or []
            if infos and infos[0].get("thumburl"):
                url = infos[0]["thumburl"]
                break
        assert url is not None, f"no thumburl for {title}"
        req2 = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req2, timeout=60) as r:
            data = r.read()
        bgr = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
        return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)

    detector = ScrfdFaceDetector(
        settings.face_detector_model,
        confidence=0.30,
        nms_threshold=0.40,
        max_regions=5,
    )

    # Test two photos:
    # 1. Closed mouth: JFK portrait
    # 2. Open mouth: Obama smiling / Trump speaking
    closed_face = _fetch_commons("File:John F. Kennedy, White House color photo portrait.jpg")
    open_face = _fetch_commons("File:President Barack Obama.jpg")

    def _analyze(name: str, img: np.ndarray) -> dict:
        dets = [r for r in detector.detect(img) if r.kps is not None]
        assert dets, f"no face in {name}"
        best = max(dets, key=lambda r: r.area)
        kps = np.asarray(best.kps, dtype=np.float32).reshape(5, 2)
        # 0: left eye, 1: right eye, 2: nose, 3: mouth_l, 4: mouth_r
        eye_dist = float(np.linalg.norm(kps[1] - kps[0]))
        mouth_width = float(np.linalg.norm(kps[4] - kps[3]))
        eye_center = (kps[0] + kps[1]) * 0.5
        mouth_center = (kps[3] + kps[4]) * 0.5
        eye_to_mouth = float(np.linalg.norm(mouth_center - eye_center))
        nose_to_mouth = float(np.linalg.norm(mouth_center - kps[2]))
        return {
            "eye_dist": eye_dist,
            "mouth_width": mouth_width,
            "eye_to_mouth": eye_to_mouth,
            "nose_to_mouth": nose_to_mouth,
            "mouth_to_eye_ratio": eye_to_mouth / max(eye_dist, 1e-4),
            "mouth_width_to_eye_ratio": mouth_width / max(eye_dist, 1e-4),
            "kps": kps.tolist(),
        }

    res = {
        "closed_jfk": _analyze("JFK", closed_face),
        "open_obama": _analyze("Obama", open_face),
    }
    print("SCRFD-PROBE-RESULTS: " + json.dumps(res, indent=2), flush=True)
    return res
