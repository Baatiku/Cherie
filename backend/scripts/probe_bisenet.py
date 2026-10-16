"""Probe BiSeNet face parser for inner mouth / teeth / tongue segmentation on Modal L4.

Run: python -m modal run scripts/probe_bisenet.py
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

app = modal.App("omnilive-bisenet-probe")

BISENET_URL = "https://huggingface.co/facefusion/models-3.1.0/resolve/main/bisenet_resnet_18.onnx"
SOURCE_REF = "C:/Users/Besta/Pictures/wixlans/image.png"


@app.function(
    image=image,
    gpu="L4",
    volumes={MODEL_ROOT: model_volume},
    secrets=[runtime_secret],
    env={"OMNILIVE_MODEL_ROOT": MODEL_ROOT},
    timeout=600,
)
def run_probe(src_bytes: bytes | None = None, tgt_bytes: bytes | None = None) -> dict:
    import base64
    import os
    import time
    import urllib.request
    import cv2
    import numpy as np
    import onnxruntime as ort

    from omnilive.face_swap import FaceSwapEngine, norm_crop, paste_back
    from omnilive.reference_image import decode_reference_image
    from omnilive.session import registry

    # 1. Ensure bisenet_resnet_18.onnx is downloaded
    bisenet_path = os.path.join(MODEL_ROOT, "parsers", "bisenet_resnet_18.onnx")
    os.makedirs(os.path.dirname(bisenet_path), exist_ok=True)
    if not os.path.isfile(bisenet_path) or os.path.getsize(bisenet_path) < 10_000_000:
        print("[download] downloading bisenet_resnet_18.onnx from HuggingFace...", flush=True)
        req = urllib.request.Request(BISENET_URL, headers={"User-Agent": "OmniLive-probe/1.0"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = resp.read()
            with open(bisenet_path, "wb") as f:
                f.write(data)
        model_volume.commit()
        print(f"[download] saved {len(data)} bytes to {bisenet_path}", flush=True)

    # 2. Load BiSeNet on GPU
    providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
    bisenet_session = ort.InferenceSession(bisenet_path, providers=providers)
    input_meta = bisenet_session.get_inputs()[0]
    print(f"[bisenet] loaded input={input_meta.name} shape={input_meta.shape} providers={bisenet_session.get_providers()}", flush=True)

    # 3. Setup FaceSwapEngine with user reference
    engine = FaceSwapEngine(swapper_engine="hyper1c")
    if src_bytes is None:
        raise ValueError("src_bytes required")

    from omnilive.models import EffectProfile, QualityProfile
    b64_src = base64.b64encode(src_bytes).decode("ascii")
    norm_src = decode_reference_image(b64_src)
    session = registry.create(QualityProfile.BALANCED, EffectProfile.FACE_SWAP, None)
    stream_key = session.session_id
    registry.set_reference_image(stream_key, norm_src)
    latent = engine.source_latent(stream_key)
    assert latent is not None, "Failed to extract latent from source image"


    # 4. Decode target aligned crop
    assert tgt_bytes is not None, "tgt_bytes required"
    tgt_bgr = cv2.imdecode(np.frombuffer(tgt_bytes, np.uint8), cv2.IMREAD_COLOR)
    aligned = cv2.cvtColor(tgt_bgr, cv2.COLOR_BGR2RGB)
    size = int(aligned.shape[0])
    template = "arcface_128"
    tgt_rgb = aligned
    transform = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float32)


    # 6. Run HyperSwap
    swapped, model_masks = engine._swap_crop(aligned, latent, size)
    assert swapped is not None

    # 7. Run BiSeNet face parser on aligned target frame
    # BiSeNet standard: 512x512 RGB normalized with mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]
    b_input = cv2.resize(aligned, (512, 512), interpolation=cv2.INTER_LINEAR)
    mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
    std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
    b_norm = (b_input.astype(np.float32) / 255.0 - mean) / std
    b_tensor = np.transpose(b_norm, (2, 0, 1))[None, ...].astype(np.float32)

    t0 = time.perf_counter()
    b_out = bisenet_session.run(None, {input_meta.name: b_tensor})[0]
    b_ms = (time.perf_counter() - t0) * 1000.0
    print(f"[bisenet] inference latency: {b_ms:.2f} ms", flush=True)

    # Argmax over classes
    # Shape of b_out: (1, 19, 512, 512)
    pred_labels = np.argmax(b_out[0], axis=0).astype(np.uint8)

    # Classes in CelebAMask-HQ:
    # 11: inner mouth (teeth, tongue, open cavity)
    # 12: upper lip
    # 13: lower lip
    inner_mouth_512 = (pred_labels == 11).astype(np.float32)
    upper_lip_512 = (pred_labels == 12).astype(np.float32)
    lower_lip_512 = (pred_labels == 13).astype(np.float32)

    # Resize inner mouth mask back to 256x256
    inner_mouth_mask = cv2.resize(inner_mouth_512, (size, size), interpolation=cv2.INTER_LINEAR)
    # Feather gently (3px blur) for seamless blending
    inner_mouth_feathered = cv2.GaussianBlur(inner_mouth_mask, (5, 5), sigmaX=1.5)
    inner_mouth_feathered = np.clip(inner_mouth_feathered, 0.0, 1.0)[..., None]

    # Blend inner mouth: swapped face gets the real speaker's teeth and tongue inside the mouth!
    swapped_with_teeth = (
        swapped.astype(np.float32) * (1.0 - inner_mouth_feathered)
        + aligned.astype(np.float32) * inner_mouth_feathered
    )
    swapped_with_teeth = np.clip(swapped_with_teeth, 0, 255).astype(np.uint8)

    # Paste both into full frame to compare
    pasted_pure_swap = paste_back(tgt_rgb, swapped, transform, model_masks or None, template)
    pasted_with_teeth = paste_back(tgt_rgb, swapped_with_teeth, transform, model_masks or None, template)

    # Encode as PNG for inspection
    _, pure_png = cv2.imencode(".png", cv2.cvtColor(pasted_pure_swap, cv2.COLOR_RGB2BGR))
    _, teeth_png = cv2.imencode(".png", cv2.cvtColor(pasted_with_teeth, cv2.COLOR_RGB2BGR))
    _, mask_png = cv2.imencode(".png", (cv2.resize(inner_mouth_512, (size, size)) * 255).astype(np.uint8))
    _, swapped_png = cv2.imencode(".png", cv2.cvtColor(swapped, cv2.COLOR_RGB2BGR))
    _, swapped_teeth_png = cv2.imencode(".png", cv2.cvtColor(swapped_with_teeth, cv2.COLOR_RGB2BGR))

    return {
        "bisenet_ms": b_ms,
        "inner_mouth_pixels": int(inner_mouth_512.sum()),
        "pure_b64": base64.b64encode(pure_png).decode("ascii"),
        "teeth_b64": base64.b64encode(teeth_png).decode("ascii"),
        "mask_b64": base64.b64encode(mask_png).decode("ascii"),
        "swapped_b64": base64.b64encode(swapped_png).decode("ascii"),
        "swapped_teeth_b64": base64.b64encode(swapped_teeth_png).decode("ascii"),
    }


@app.local_entrypoint()
def main():
    import base64
    from pathlib import Path

    import cv2
    src_file = Path("C:/Users/Besta/Pictures/wixlans/image.png")
    assert src_file.is_file(), f"Source image not found: {src_file}"
    img = cv2.imread(str(src_file), cv2.IMREAD_COLOR)

    h, w = img.shape[:2]
    scale = min(1.0, 1024.0 / max(h, w))
    if scale < 1.0:
        img = cv2.resize(img, (max(32, int(w * scale)), max(32, int(h * scale))), interpolation=cv2.INTER_AREA)
    _, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
    src_bytes = bytes(buf)

    tgt_file = Path("scratch_probe_user/aligned_hyper1c.png")
    assert tgt_file.is_file(), f"Target crop not found: {tgt_file}"
    tgt_bytes = tgt_file.read_bytes()

    print("Launching BiSeNet probe on Modal L4 GPU...", flush=True)
    res = run_probe.remote(src_bytes, tgt_bytes)

    print(f"Probe completed! BiSeNet latency: {res['bisenet_ms']:.2f} ms, inner mouth pixels: {res['inner_mouth_pixels']}")

    out_dir = Path("scratch_bisenet")
    out_dir.mkdir(exist_ok=True)
    (out_dir / "pasted_pure_swap.png").write_bytes(base64.b64decode(res["pure_b64"]))
    (out_dir / "pasted_with_teeth.png").write_bytes(base64.b64decode(res["teeth_b64"]))
    (out_dir / "mouth_mask.png").write_bytes(base64.b64decode(res["mask_b64"]))
    (out_dir / "swapped_pure.png").write_bytes(base64.b64decode(res["swapped_b64"]))
    (out_dir / "swapped_with_teeth.png").write_bytes(base64.b64decode(res["swapped_teeth_b64"]))
    print(f"Saved results to {out_dir}/")
