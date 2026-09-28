import modal

MODEL_ROOT="/models"
model_volume=modal.Volume.from_name("omnilive-models",create_if_missing=True)
runtime_secret=modal.Secret.from_name(
    "omnilive-runtime",
    required_keys=["OMNILIVE_SESSION_SECRET"],
)

image=(
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("libgl1","libglib2.0-0")
    .pip_install(
        "fastapi>=0.115,<1",
        "uvicorn[standard]>=0.30,<1",
        "pydantic>=2.8,<3",
        "aiortc>=1.9,<2",
        "av>=12,<16",
        "numpy>=2,<3",
        "opencv-python-headless>=4.10,<5",
        "onnxruntime-gpu>=1.20,<2",
    )
    .add_local_python_source("omnilive")
)

app=modal.App("omnilive")

@app.function(
    image=image,
    gpu="L4",
    volumes={MODEL_ROOT:model_volume},
    secrets=[runtime_secret],
    env={"OMNILIVE_MODEL_ROOT":MODEL_ROOT},
    scaledown_window=300,
    timeout=3600,
)
@modal.concurrent(max_inputs=1)
@modal.asgi_app()
def api():
    from omnilive.processor import processor
    processor.preload()
    from omnilive.app import app as fastapi_app
    return fastapi_app
