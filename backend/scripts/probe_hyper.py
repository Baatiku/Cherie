"""Decide the HyperSwap mask polarity: paste with mask as-is, inverted, or oval-only.

Run from backend/:  python -m modal run scripts/probe_hyper.py
Reports ArcFace cosine to the source for each variant. Highest wins.
"""

from __future__ import annotations

import modal

MODEL_ROOT = "/models"
model_volume = modal.Volume.from_name("omnilive-models", create_if_missing=True)

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

app = modal.App("omnilive-hyper-probe")

SOURCE_CANDIDATES = [
    "https://raw.githubusercontent.com/opencv/opencv/master/samples/data/lena.jpg",
]
TARGET_CANDIDATES = [
    "https://raw.githubusercontent.com/scikit-image/scikit-image/v0.21.0/skimage/data/astronaut.png",
]


@app.function(image=image, gpu="L4", volumes={MODEL_ROOT: model_volume}, timeout=1200)
def probe() -> dict:
    import urllib.request

    import cv2
    import numpy as np

    from omnilive.face_swap import FaceSwapEngine, _face_oval, norm_crop, paste_back

    UA = {"User-Agent": "OmniLive-probe/0.7"}

    def fetch(candidates: list[str]) -> np.ndarray:
        for url in candidates:
            try:
                request = urllib.request.Request(url, headers=UA)
                with urllib.request.urlopen(request, timeout=60) as response:
                    data = response.read()
                if len(data) > 20_000:
                    img = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
                    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                    scale = 540.0 / max(1, rgb.shape[0])
                    return cv2.resize(
                        rgb,
                        (max(2, int(rgb.shape[1] * scale)), max(2, int(rgb.shape[0] * scale))),
                        interpolation=cv2.INTER_AREA,
                    )
            except Exception:
                continue
        raise RuntimeError("no photo")

    source = fetch(SOURCE_CANDIDATES)
    target = fetch(TARGET_CANDIDATES)

    engine = FaceSwapEngine(swapper_engine="hyper1c")

    def raw_embedding(image: np.ndarray):
        faces = [r for r in engine.detector.detect(image) if r.kps is not None]
        assert faces, "no face"
        kps = np.asarray(max(faces, key=lambda r: r.area).kps, dtype=np.float32)
        aligned, _ = norm_crop(image, kps.reshape(5, 2), 112, "arcface_112")
        assert aligned is not None
        arcface = engine.arcface.load()
        assert arcface is not None
        meta = arcface.get_inputs()[0]
        blob = cv2.dnn.blobFromImage(
            aligned, 1.0 / 127.5, (112, 112), (127.5, 127.5, 127.5), swapRB=False
        )
        vector = np.asarray(arcface.run(None, {meta.name: blob})[0]).reshape(-1)
        return vector / (float(np.linalg.norm(vector)) + 1e-9)

    src_vector = raw_embedding(source)

    from onnx import numpy_helper
    import onnx as _onnx

    emap_model = _onnx.load("/models/swapper/inswapper_128_fp16.onnx")
    emap = numpy_helper.to_array(emap_model.graph.initializer[-1]).astype(np.float32)
    mapped_vector = src_vector.reshape(1, -1) @ emap
    mapped_vector = (mapped_vector / (float(np.linalg.norm(mapped_vector)) + 1e-9)).astype(np.float32)

    faces = [r for r in engine.detector.detect(target) if r.kps is not None]
    assert faces, "no face in target"
    tkps = np.asarray(max(faces, key=lambda r: r.area).kps, dtype=np.float32).reshape(5, 2)
    aligned, transform = norm_crop(target, tkps, 256, "arcface_128")
    assert aligned is not None and transform is not None

    latent = src_vector.reshape(1, -1).astype(np.float32)
    blob = cv2.dnn.blobFromImage(
        aligned, 1.0 / 127.5, (256, 256), (127.5, 127.5, 127.5), swapRB=False
    )
    session = engine.swapper.load()
    assert session is not None
    inputs = session.get_inputs()
    live_name = next((m.name for m in inputs if "target" in m.name.lower()), inputs[0].name)
    ref_name = next((m.name for m in inputs if m.name != live_name), None)
    feeds = {live_name: blob}
    if ref_name:
        feeds[ref_name] = latent
    results = session.run(None, feeds)
    out_arr, mask_arr = (np.asarray(r) for r in results[:2])
    swapped = np.clip(out_arr[0].transpose(1, 2, 0) * 127.5 + 127.5, 0, 255).astype(np.uint8)
    model_mask = np.clip(mask_arr[0, 0], 0.0, 1.0).astype(np.float32)

    oval = (_face_oval(256, "arcface_128")).astype(np.float32)
    report = {}
    for label, latent_vec, extra in (
        ("plain+oval", src_vector.reshape(1, -1).astype(np.float32), []),
        ("mapped+oval", mapped_vector, []),
        ("mapped+mask", mapped_vector, [model_mask]),
    ):
        feeds = {live_name: blob}
        if ref_name:
            feeds[ref_name] = latent_vec
        run_results = session.run(None, feeds)
        run_out = np.asarray(run_results[0])
        run_swapped = np.clip(
            run_out[0].transpose(1, 2, 0) * 127.5 + 127.5, 0, 255
        ).astype(np.uint8)
        pasted = paste_back(target, run_swapped, transform, extra or None, "arcface_128")
        faces = [r for r in engine.detector.detect(pasted) if r.kps is not None]
        if not faces:
            report[label] = {"identity": None, "note": "no face detected in output"}
            continue
        kps = np.asarray(max(faces, key=lambda r: r.area).kps, dtype=np.float32)
        out_aligned, _ = norm_crop(pasted, kps.reshape(5, 2), 112, "arcface_112")
        assert out_aligned is not None
        blob_out = cv2.dnn.blobFromImage(
            out_aligned, 1.0 / 127.5, (112, 112), (127.5, 127.5, 127.5), swapRB=False
        )
        arcface = engine.arcface.load()
        assert arcface is not None
        meta = arcface.get_inputs()[0]
        vector = np.asarray(arcface.run(None, {meta.name: blob_out})[0]).reshape(-1)
        vector = vector / (float(np.linalg.norm(vector)) + 1e-9)
        report[label] = {"identity": round(float(np.dot(src_vector, vector)), 4)}
    import json

    print("PROBE-RESULT " + json.dumps(report), flush=True)
    return report
