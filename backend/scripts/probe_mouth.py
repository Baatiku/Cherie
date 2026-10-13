"""Probe mouth opening and teeth transfer across swapper engines and masks.

Run from backend/:  python -m modal run scripts/probe_mouth.py
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

app = modal.App("omnilive-mouth-probe")

SOURCE_URL = "https://raw.githubusercontent.com/opencv/opencv/master/samples/data/lena.jpg"
# Steve Ballmer speaking with open mouth
TARGET_URL = "commons:File:Steve Ballmer 2014.jpg"


@app.function(
    image=image,
    gpu="L4",
    volumes={MODEL_ROOT: model_volume},
    secrets=[runtime_secret],
    env={"OMNILIVE_MODEL_ROOT": MODEL_ROOT},
    timeout=600,
)
def probe(src_bytes_override: bytes | None = None) -> dict:
    import json
    import urllib.parse
    import urllib.request
    import cv2
    import numpy as np

    from omnilive.face_swap import FaceSwapEngine, norm_crop, paste_back, _face_oval
    from omnilive.models import EffectProfile, QualityProfile
    from omnilive.reference_image import decode_reference_image
    from omnilive.session import registry

    UA = {"User-Agent": "OmniLive-probe/0.7"}

    def _fetch(url: str) -> bytes:
        if url.startswith("commons:"):
            title = url.split(":", 1)[1]
            api = (
                "https://commons.wikimedia.org/w/api.php?action=query&titles="
                + urllib.parse.quote(title)
                + "&prop=imageinfo&iiprop=url&iiurlwidth=512&format=json"
            )
            req = urllib.request.Request(api, headers=UA)
            with urllib.request.urlopen(req, timeout=30) as r:
                payload = json.loads(r.read().decode("utf-8"))
            pages = payload.get("query", {}).get("pages", {})
            for page in pages.values():
                infos = page.get("imageinfo") or []
                if infos and infos[0].get("thumburl"):
                    url = infos[0]["thumburl"]
                    break
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read()

    src_bytes = src_bytes_override if src_bytes_override is not None else _fetch(SOURCE_URL)
    tgt_bytes = _fetch(TARGET_URL)
    print(f"FETCHED src={len(src_bytes)} bytes, tgt={len(tgt_bytes)} bytes", flush=True)

    src_img = cv2.imdecode(np.frombuffer(src_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
    tgt_img = cv2.imdecode(np.frombuffer(tgt_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert src_img is not None, "src_img decode failed"
    assert tgt_img is not None, "tgt_img decode failed"
    src_rgb = cv2.cvtColor(src_img, cv2.COLOR_BGR2RGB)
    tgt_rgb = cv2.cvtColor(tgt_img, cv2.COLOR_BGR2RGB)

    import base64
    ref = decode_reference_image(base64.b64encode(src_bytes))
    session = registry.create(QualityProfile.BALANCED, EffectProfile.FACE_SWAP, None)
    stream_key = session.session_id
    success = registry.set_reference_image(stream_key, ref)
    print(f"REGISTERED ref success={success}, sha={ref.sha256}, size={ref.width}x{ref.height}", flush=True)

    results = {}

    for engine_name in ["hyper1c", "inswapper", "hyper1b"]:
        print(f"TESTING {engine_name}...", flush=True)
        engine = FaceSwapEngine(swapper_engine=engine_name)
        rgb, sha = engine._reference_rgb(stream_key)
        print(f"REF RGB: {rgb is not None}, sha: {sha}", flush=True)
        dets = [r for r in engine.detector.detect(rgb) if r.kps is not None]
        print(f"DETS in ref: {len(dets)}", flush=True)
        arc = engine.arcface.load()
        print(f"ARCFACE LOADED: {arc is not None}, arcface err: {engine.arcface.last_error}", flush=True)
        swp = engine.swapper.load()
        print(f"SWAPPER LOADED: {swp is not None}, swapper err: {engine.swapper.last_error}", flush=True)
        latent = engine.source_latent(stream_key)
        print(f"ENGINE {engine_name} latent is None? {latent is None}", flush=True)
        assert latent is not None, f"Failed latent for {engine_name}"

        # Detect face in target
        faces = [r for r in engine.detector.detect(tgt_rgb) if r.kps is not None]
        assert faces, "No face detected in target"
        best = max(faces, key=lambda r: r.area)
        kps = np.asarray(best.kps, dtype=np.float32).reshape(5, 2)

        size = int(engine.spec["crop"])
        template = str(engine.spec["template"])
        aligned, transform = norm_crop(tgt_rgb, kps, size, template)
        assert aligned is not None

        # Analyze mouth region in aligned crop (lower third, central width)
        # In arcface_128/112, mouth is around y = 0.65 to 0.85, x = 0.35 to 0.65
        my0, my1 = int(size * 0.65), int(size * 0.85)
        mx0, mx1 = int(size * 0.35), int(size * 0.65)
        target_mouth = aligned[my0:my1, mx0:mx1]

        # Swapper inference
        swapped, model_masks = engine._swap_crop(aligned, latent, size)
        assert swapped is not None, f"Swapper produced no face for {engine_name}"

        swapped_mouth = swapped[my0:my1, mx0:mx1]

        # Occluder mask
        xseg_mask = engine._occlusion(aligned, size)

        # Check model mask in mouth region
        mm_mouth_mean = None
        if model_masks:
            mm_mouth_mean = float(model_masks[0][my0:my1, mx0:mx1].mean())

        xseg_mouth_mean = None
        if xseg_mask is not None:
            xseg_mouth_mean = float(xseg_mask[my0:my1, mx0:mx1].mean())

        # Test paste_back with different mask combos
        # 1. Full masks (model_masks + xseg)
        all_masks = list(model_masks)
        if xseg_mask is not None:
            all_masks.append(xseg_mask)
        pasted_all = paste_back(tgt_rgb, swapped, transform, all_masks or None, template)

        # 2. Model masks only
        pasted_model_only = paste_back(tgt_rgb, swapped, transform, model_masks or None, template)

        # 3. Oval only
        pasted_oval_only = paste_back(tgt_rgb, swapped, transform, None, template)

        # Encode aligned and swapped crops as base64 PNG to inspect visual mouth opening
        _, aligned_png = cv2.imencode(".png", cv2.cvtColor(aligned, cv2.COLOR_RGB2BGR))
        _, swapped_png = cv2.imencode(".png", cv2.cvtColor(swapped, cv2.COLOR_RGB2BGR))
        _, pasted_png = cv2.imencode(".png", cv2.cvtColor(pasted_all, cv2.COLOR_RGB2BGR))

        import base64
        results[engine_name] = {
            "target_mouth_mean_lum": float(cv2.cvtColor(target_mouth, cv2.COLOR_RGB2GRAY).mean()),
            "target_mouth_std_lum": float(cv2.cvtColor(target_mouth, cv2.COLOR_RGB2GRAY).std()),
            "swapped_mouth_mean_lum": float(cv2.cvtColor(swapped_mouth, cv2.COLOR_RGB2GRAY).mean()),
            "swapped_mouth_std_lum": float(cv2.cvtColor(swapped_mouth, cv2.COLOR_RGB2GRAY).std()),
            "model_masks_count": len(model_masks),
            "model_mask_mouth_mean": mm_mouth_mean,
            "xseg_mask_mouth_mean": xseg_mouth_mean,
            "aligned_b64": base64.b64encode(aligned_png).decode("ascii"),
            "swapped_b64": base64.b64encode(swapped_png).decode("ascii"),
            "pasted_b64": base64.b64encode(pasted_png).decode("ascii"),
        }

    print("PROBE FINISHED", flush=True)
    return results


@app.local_entrypoint()
def main():
    import base64
    from pathlib import Path
    import cv2
    out_dir = Path("c:/Users/Besta/OmiLive/scratch_probe_user")
    out_dir.mkdir(parents=True, exist_ok=True)
    user_ref_path = Path(r"C:\Users\Besta\Pictures\wixlans\image.png")
    if user_ref_path.exists():
        img = cv2.imread(str(user_ref_path), cv2.IMREAD_COLOR)
        h, w = img.shape[:2]
        scale = min(1.0, 1024.0 / max(h, w))
        if scale < 1.0:
            img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
        _, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
        user_bytes = bytes(buf)
    else:
        user_bytes = None
    data = probe.remote(user_bytes)
    for eng, res in data.items():
        with open(out_dir / f"aligned_{eng}.png", "wb") as f:
            f.write(base64.b64decode(res["aligned_b64"]))
        with open(out_dir / f"swapped_{eng}.png", "wb") as f:
            f.write(base64.b64decode(res["swapped_b64"]))
        with open(out_dir / f"pasted_{eng}.png", "wb") as f:
            f.write(base64.b64decode(res["pasted_b64"]))
        print(f"Saved images for {eng} in {out_dir}", flush=True)
