"""Download face-swap weights into the omnilive-models Modal Volume.

Run from backend/:  python -m modal run scripts/provision_swap_models.py

Also inspects the swapper graph (inputs/outputs/embedding map) so the
live adapter can be wired to the exact model contract.
"""

from __future__ import annotations

import modal

MODEL_ROOT = "/models"
model_volume = modal.Volume.from_name("omnilive-models", create_if_missing=True)

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install(
        "numpy>=2,<3",
        "onnx>=1.17,<2",
        "onnxruntime>=1.20,<2",
        "opencv-python-headless>=4.10,<5",
    )
)

app = modal.App("omnilive-model-provision")

FILES = {
    # SCRFD-10G detector with keypoints (official buffalo_l pack mirror).
    "detectors/det_10g.onnx": (
        "https://huggingface.co/public-data/insightface"
        "/resolve/main/models/buffalo_l/det_10g.onnx"
    ),
    # ArcFace ResNet50 identity encoder (official buffalo_l pack mirror).
    "recognition/w600k_r50.onnx": (
        "https://huggingface.co/public-data/insightface"
        "/resolve/main/models/buffalo_l/w600k_r50.onnx"
    ),
    # 128px real-time swapper, fp16 (ships in a live-cam project).
    "swapper/inswapper_128_fp16.onnx": (
        "https://huggingface.co/hacksider/deep-live-cam"
        "/resolve/main/inswapper_128_fp16.onnx"
    ),
}


@app.function(image=image, volumes={MODEL_ROOT: model_volume}, timeout=3600)
def fetch() -> dict:
    import os
    import urllib.request

    report: dict = {"files": {}, "swapper": {}, "detector": {}, "arcface": {}}
    for relative, url in FILES.items():
        dest = os.path.join(MODEL_ROOT, relative)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if os.path.isfile(dest) and os.path.getsize(dest) > 1_000_000:
            report["files"][relative] = {"status": "already_present", "bytes": os.path.getsize(dest)}
            continue
        tmp = dest + ".part"
        urllib.request.urlretrieve(url, tmp)
        os.replace(tmp, dest)
        report["files"][relative] = {"status": "downloaded", "bytes": os.path.getsize(dest)}
    model_volume.commit()

    import numpy as np
    import onnx
    from onnx import numpy_helper

    swapper_path = os.path.join(MODEL_ROOT, "swapper/inswapper_128_fp16.onnx")
    model = onnx.load(swapper_path)
    emap = numpy_helper.to_array(model.graph.initializer[-1])
    report["swapper"]["emap_shape"] = list(emap.shape)
    report["swapper"]["emap_dtype"] = str(emap.dtype)

    import onnxruntime as ort

    for label, relative, key in (
        ("swapper", "swapper/inswapper_128_fp16.onnx", "swapper"),
        ("detector", "detectors/det_10g.onnx", "detector"),
        ("arcface", "recognition/w600k_r50.onnx", "arcface"),
    ):
        session = ort.InferenceSession(
            os.path.join(MODEL_ROOT, relative), providers=["CPUExecutionProvider"]
        )
        report[key]["inputs"] = [
            {"name": i.name, "shape": [str(d) for d in i.shape], "type": i.type}
            for i in session.get_inputs()
        ]
        report[key]["outputs"] = [
            {"name": o.name, "shape": [str(d) for d in o.shape], "type": o.type}
            for o in session.get_outputs()
        ]
    print(report)
    return report
