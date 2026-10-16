"""Probe face swap alignment, BiSeNet classes, and jaw dynamics on open mouth.

Run: cmd.exe /c "set PYTHONIOENCODING=utf-8&& set PYTHONUTF8=1&& set PYTHONPATH=backend&& C:\Python313\python.exe -m modal run backend/scripts/probe_open_mouth.py"
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

app = modal.App("omnilive-open-mouth-probe")


@app.function(
    image=image,
    gpu="L4",
    volumes={MODEL_ROOT: model_volume},
    secrets=[runtime_secret],
    env={"OMNILIVE_MODEL_ROOT": MODEL_ROOT},
    timeout=600,
)
def run_probe(src_bytes: bytes, tgt_bytes: bytes) -> dict:
    import base64
    import os
    import time
    import cv2
    import numpy as np
    import onnxruntime as ort

    from omnilive.face_swap import FaceSwapEngine, norm_crop, paste_back, _face_oval, _template_points
    from omnilive.reference_image import decode_reference_image
    from omnilive.session import registry
    from omnilive.models import EffectProfile, QualityProfile

    # 1. Load BiSeNet
    bisenet_path = os.path.join(MODEL_ROOT, "parsers", "bisenet_resnet_18.onnx")
    providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
    bisenet_session = ort.InferenceSession(bisenet_path, providers=providers)
    input_meta = bisenet_session.get_inputs()[0]

    # 2. Setup engine & extract latent
    engine = FaceSwapEngine(swapper_engine="hyper1c")
    b64_src = base64.b64encode(src_bytes).decode("ascii")
    norm_src = decode_reference_image(b64_src)
    session = registry.create(QualityProfile.BALANCED, EffectProfile.FACE_SWAP, None)
    stream_key = session.session_id
    registry.set_reference_image(stream_key, norm_src)
    latent = engine.source_latent(stream_key)
    assert latent is not None, "Failed to extract latent"

    # 3. Decode target crop (aligned 256x256)
    tgt_bgr = cv2.imdecode(np.frombuffer(tgt_bytes, np.uint8), cv2.IMREAD_COLOR)
    aligned = cv2.cvtColor(tgt_bgr, cv2.COLOR_BGR2RGB)
    size = int(aligned.shape[0])

    # 4. Run HyperSwap
    swapped, model_masks = engine._swap_crop(aligned, latent, size)
    assert swapped is not None

    # 5. Run BiSeNet
    b_input = cv2.resize(aligned, (512, 512), interpolation=cv2.INTER_LINEAR)
    mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
    std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
    b_norm = (b_input.astype(np.float32) / 255.0 - mean) / std
    b_tensor = np.transpose(b_norm, (2, 0, 1))[None, ...].astype(np.float32)
    b_out = bisenet_session.run(None, {input_meta.name: b_tensor})[0]
    pred = np.argmax(b_out[0], axis=0).astype(np.uint8)

    inner_mouth = cv2.resize((pred == 11).astype(np.float32), (size, size))
    lower_lip = cv2.resize((pred == 13).astype(np.float32), (size, size))
    skin_mask = cv2.resize((pred == 1).astype(np.float32), (size, size))

    # Test 1: Only inner mouth (current)
    feather_11 = cv2.GaussianBlur(inner_mouth, (5, 5), sigmaX=1.5)[..., None]
    blend_11 = (swapped.astype(np.float32) * (1.0 - feather_11) + aligned.astype(np.float32) * feather_11)
    blend_11 = np.clip(blend_11, 0, 255).astype(np.uint8)

    # Test 2: Natural mouth + articulated lower lip (adaptive)
    # When mouth opens wide, the live lower lip moves with the jaw and teeth
    mouth_articulated = np.maximum(inner_mouth, lower_lip * 0.90)
    feather_art = cv2.GaussianBlur(mouth_articulated, (5, 5), sigmaX=1.5)[..., None]
    blend_art = (swapped.astype(np.float32) * (1.0 - feather_art) + aligned.astype(np.float32) * feather_art)
    blend_art = np.clip(blend_art, 0, 255).astype(np.uint8)

    # Test 3: Jaw-extended solid oval mask (no premature fade-out at y=220)
    # Build anatomical oval that extends down to y=245 for the jaw
    dst_points = _template_points("arcface_128", size)
    left_eye, right_eye = dst_points[0], dst_points[1]
    eye_dist = float(np.linalg.norm(right_eye - left_eye))
    cx = float((left_eye[0] + right_eye[0]) * 0.5)
    cy = float((left_eye[1] + right_eye[1]) * 0.5 + eye_dist * 0.65)
    rx = eye_dist * 1.65
    ry = eye_dist * 2.25  # extended from 1.95: covers full jaw to y=248

    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    oval = (((xx - cx) / rx) ** 2 + ((yy - cy) / ry) ** 2) <= 1.0
    mask_jaw = np.zeros((size, size), dtype=np.float32)
    mask_jaw[oval] = 1.0
    mask_jaw = cv2.GaussianBlur(mask_jaw, (9, 9), sigmaX=3.0)
    # Soft box boundary feathering only at the absolute 4px crop boundary
    mask_jaw[:4, :] *= np.linspace(0.0, 1.0, 4)[:, None]
    mask_jaw[-4:, :] *= np.linspace(1.0, 0.0, 4)[:, None]
    mask_jaw[:, :4] *= np.linspace(0.0, 1.0, 4)[None, :]
    mask_jaw[:, -4:] *= np.linspace(1.0, 0.0, 4)[None, :]

    transform = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float32)

    # Paste-back with old static oval
    pasted_old = paste_back(aligned, blend_11, transform, template="arcface_128")

    # Paste-back with new jaw-solid mask + articulated mouth
    pasted_new = paste_back(aligned, blend_art, transform, extra_masks=[mask_jaw], template="arcface_128")

    def _enc(im):
        if im.ndim == 2:
            im = (np.clip(im, 0.0, 1.0) * 255.0).astype(np.uint8)
            im = cv2.cvtColor(im, cv2.COLOR_GRAY2BGR)
        else:
            im = cv2.cvtColor(im, cv2.COLOR_RGB2BGR)
        _, buf = cv2.imencode(".png", im)
        return base64.b64encode(buf).decode("ascii")

    return {
        "aligned": _enc(aligned),
        "swapped": _enc(swapped),
        "blend_11": _enc(blend_11),
        "blend_art": _enc(blend_art),
        "mask_inner": _enc(inner_mouth),
        "mask_lower_lip": _enc(lower_lip),
        "mask_jaw": _enc(mask_jaw),
        "pasted_old": _enc(pasted_old),
        "pasted_new": _enc(pasted_new),
        "inner_pixels": int(inner_mouth.sum()),
        "lower_lip_pixels": int(lower_lip.sum()),
    }


@app.local_entrypoint()
def main():
    import base64
    from pathlib import Path
    import cv2

    src_file = Path("C:/Users/Besta/Pictures/wixlans/image.png")
    img = cv2.imread(str(src_file), cv2.IMREAD_COLOR)
    h, w = img.shape[:2]
    scale = min(1.0, 1024.0 / max(h, w))
    if scale < 1.0:
        img = cv2.resize(img, (max(32, int(w * scale)), max(32, int(h * scale))), interpolation=cv2.INTER_AREA)
    _, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
    src_bytes = bytes(buf)

    tgt_file = Path("scratch_probe_user/aligned_hyper1c.png")
    tgt_bytes = tgt_file.read_bytes()

    print("Launching open mouth probe on Modal L4...", flush=True)
    res = run_probe.remote(src_bytes, tgt_bytes)
    print(f"Probe done! inner_pixels={res['inner_pixels']}, lower_lip_pixels={res['lower_lip_pixels']}")

    out_dir = Path("scratch_open_mouth")
    out_dir.mkdir(exist_ok=True)
    for k in ("aligned", "swapped", "blend_11", "blend_art", "mask_inner", "mask_lower_lip", "mask_jaw", "pasted_old", "pasted_new"):
        (out_dir / f"{k}.png").write_bytes(base64.b64decode(res[k]))
    print(f"Saved probe images to {out_dir}/")
