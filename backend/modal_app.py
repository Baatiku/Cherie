import modal
import os

MODEL_ROOT="/models"
# Propagate the deployed git SHA so /health.build_sha matches the tested
# commit on Modal (Render sets RENDER_GIT_COMMIT, Modal does not).
BUILD_SHA = os.getenv("OMNILIVE_BUILD_SHA") or os.getenv("RENDER_GIT_COMMIT") or "unknown"
model_volume=modal.Volume.from_name("omnilive-models",create_if_missing=True)
runtime_secret=modal.Secret.from_name(
    "omnilive-runtime",
    required_keys=["OMNILIVE_SESSION_SECRET"],
)

image=(
    # CUDA base image: onnxruntime-gpu needs real CUDA + cuDNN libraries,
    # which the slim Debian image does not ship (without them the swapper
    # silently falls back to CPU and runs ~100x too slow for live video).
    modal.Image.from_registry(
        "nvidia/cuda:12.6.3-cudnn-runtime-ubuntu22.04", add_python="3.12"
    )
    .apt_install("libgl1","libglib2.0-0")
    .pip_install(
        "fastapi>=0.115,<1",
        "uvicorn[standard]>=0.30,<1",
        "pydantic>=2.8,<3",
        "aiortc>=1.9,<2",
        "av>=12,<16",
        "numpy>=2,<3",
        "opencv-python-headless>=4.10,<5",
        "onnx>=1.17,<2",
        # Pinned: 1.22.x is the CUDA 12 + cuDNN 9 line matching the image
        # below. 1.23+ requires CUDA 13 and fails to load GPU providers.
        "onnxruntime-gpu==1.22.0",
    )
    .add_local_python_source("omnilive")
)

app=modal.App("omnilive")

@app.function(
    image=image,
    gpu="L4",
    volumes={MODEL_ROOT:model_volume},
    secrets=[runtime_secret],
    env={"OMNILIVE_MODEL_ROOT": MODEL_ROOT, "OMNILIVE_BUILD_SHA": BUILD_SHA, "OMNILIVE_PRODUCTION": "1"},
    scaledown_window=300,
    timeout=3600,
    min_containers=0,
)
@modal.concurrent(max_inputs=1)
@modal.asgi_app()
def api():
    from omnilive.processor import processor
    processor.preload()
    from omnilive.app import app as fastapi_app
    return fastapi_app
