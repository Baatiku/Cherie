try:
    import modal
except ImportError:
    modal = None

if modal is not None:
    image = modal.Image.debian_slim(python_version="3.12").pip_install(
        "fastapi>=0.115,<1", "uvicorn[standard]>=0.30,<1", "pydantic>=2.8,<3"
    ).add_local_python_source("omnilive")
    app = modal.App("omnilive")

    @app.function(image=image, gpu="L4", scaledown_window=300, timeout=3600)
    def warm_gpu_worker(session_id: str):
        return {"session_id": session_id, "gpu": "L4", "ready": True}
