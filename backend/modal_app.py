import modal

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install(
        "fastapi>=0.115,<1",
        "uvicorn[standard]>=0.30,<1",
        "pydantic>=2.8,<3",
        "aiortc>=1.9,<2",
        "av>=12,<16",
    )
    .add_local_python_source("omnilive")
)

app = modal.App("omnilive")

@app.function(
    image=image,
    gpu="L4",
    scaledown_window=300,
    timeout=3600,
)
@modal.asgi_app()
def api():
    from omnilive.app import app as fastapi_app
    return fastapi_app
