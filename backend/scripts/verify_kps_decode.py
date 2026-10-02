"""Decide the det_10g keypoint convention empirically (stride-scaled or not).

Run from backend/:  python -m modal run scripts/verify_kps_decode.py

Decodes both variants on a real photo and scores each by whether the 5
points land inside their own box with sane facial anatomy. Prints the
winner. CPU-only, finishes in about a minute.
"""

from __future__ import annotations

import modal

MODEL_ROOT = "/models"
model_volume = modal.Volume.from_name("omnilive-models", create_if_missing=True)

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("numpy>=2,<3", "opencv-python-headless>=4.10,<5", "onnxruntime>=1.20,<2")
    .add_local_python_source("omnilive")
)

app = modal.App("omnilive-kps-verify")

CANDIDATES = [
    "https://raw.githubusercontent.com/opencv/opencv/master/samples/data/lena.jpg",
    "https://upload.wikimedia.org/wikipedia/en/7/7d/Lenna_%28test_image%29.png",
]


@app.function(image=image, volumes={MODEL_ROOT: model_volume}, timeout=600)
def verify() -> dict:
    import os
    import urllib.request

    import cv2
    import numpy as np
    import onnxruntime as ort

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
        return {"ok": False}

    img = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
    height, width = img.shape[:2]
    target_side = 640
    scale = min(1.0, target_side / float(max(height, width)))
    resized = cv2.resize(
        img, (max(1, int(width * scale)), max(1, int(height * scale))), interpolation=cv2.INTER_AREA
    )
    target_h, target_w = resized.shape[:2]
    blob = (resized.astype(np.float32) - 127.5) / 128.0
    blob = np.transpose(blob, (2, 0, 1))[None, ...]

    session = ort.InferenceSession(
        os.path.join(MODEL_ROOT, "detectors/det_10g.onnx"),
        providers=["CPUExecutionProvider"],
    )
    outputs = [np.asarray(v) for v in session.run(None, {"input.1": blob})]
    scores = [np.asarray(outputs[i]).reshape(-1) for i in (0, 1, 2)]
    boxes = [np.asarray(outputs[i]).reshape(-1, 4).astype(np.float32) for i in (3, 4, 5)]
    kps_raw = [np.asarray(outputs[i]).reshape(-1, 10).astype(np.float32) for i in (6, 7, 8)]

    def decode(stride_mult: bool):
        found = []
        for level, stride in enumerate((8, 16, 32)):
            level_scores = scores[level]
            level_boxes = boxes[level]
            level_kps = kps_raw[level]
            count = min(len(level_scores), len(level_boxes), len(level_kps))
            feature_h = max(1, target_h // stride)
            feature_w = max(1, target_w // stride)
            yy, xx = np.mgrid[0:feature_h, 0:feature_w]
            centers = (
                np.stack((xx, yy), axis=-1).reshape(-1, 2).astype(np.float32) * float(stride)
            )
            anchors = max(1, int(np.ceil(count / float(feature_h * feature_w))))
            if anchors > 1:
                centers = np.repeat(centers, anchors, axis=0)
            centers = centers[:count]
            for i in range(count):
                if level_scores[i] < 0.5:
                    continue
                delta = level_boxes[i] * (float(stride) if True else 1.0)
                cx, cy = centers[i]
                x0, y0 = (cx - delta[0]), (cy - delta[1])
                x1, y1 = (cx + delta[2]), (cy + delta[3])
                row = level_kps[i].reshape(5, 2)
                if stride_mult:
                    pts = centers[i] + row * float(stride)
                else:
                    pts = centers[i] + row
                found.append(
                    (
                        float(level_scores[i]),
                        (x0 / target_w * width, y0 / target_h * height,
                         x1 / target_w * width, y1 / target_h * height),
                        [(float(px) / target_w * width, float(py) / target_h * height) for px, py in pts],
                    )
                )
        found.sort(key=lambda item: -item[0])
        return found[:3]

    def anatomy(box, pts) -> dict:
        x0, y0, x1, y1 = box
        bw, bh = max(1.0, x1 - x0), max(1.0, y1 - y0)
        inside = sum(1 for px, py in pts if x0 - 0.05 * bw <= px <= x1 + 0.05 * bw and y0 - 0.05 * bh <= py <= y1 + 0.05 * bh)
        (lx, ly), (rx, ry), (nx, ny), (_, my1), (_, my2) = pts
        eye_gap = abs(rx - lx) / bw
        ordered = ly < ny < my1 and ry < ny < my2
        return {"inside": inside, "eye_gap": round(eye_gap, 3), "y_order_ok": bool(ordered)}

    report = {}
    for variant in (True, False):
        faces = decode(stride_mult=variant)
        report["stride_mult" if variant else "no_stride"] = [
            {"score": round(s, 3), "box": [round(v, 1) for v in b], **anatomy(b, p)}
            for s, b, p in faces
        ]
    print(report)
    return report
