import asyncio
import base64
import os
import time
from typing import Any

import modal

PYTHON = "3.12"
APP_NAME = "omnicam-live"
MODEL_PATH = "/models/inswapper_128.onnx"
VIDEO_WIDTH = 640
VIDEO_HEIGHT = 480
ANSWER_TIMEOUT = 300.0
MAX_IDENTITY_BYTES = 4 * 1024 * 1024

gpu_image = (
    modal.Image.debian_slim(python_version=PYTHON)
    .apt_install(
        "ffmpeg",
        "libgl1",
        "libglib2.0-0",
        "build-essential",
        "cmake",
    )
    .uv_pip_install(
        "pipecat-ai[webrtc]==1.5.0",
        "fastapi==0.115.12",
        "numpy<3",
        "opencv-python-headless==4.11.0.86",
        "cython<4",
        "insightface==0.7.3",
        "onnxruntime-gpu==1.21.0",
    )
)

server_image = (
    modal.Image.debian_slim(python_version=PYTHON)
    .uv_pip_install("fastapi==0.115.12")
)

app = modal.App(APP_NAME)
models = modal.Volume.from_name(
    "omnicam-models",
    create_if_missing=True,
)
config_secret = modal.Secret.from_name("omnicam-config")


def _ice_servers() -> list[dict[str, Any]]:
    servers: list[dict[str, Any]] = [
        {"urls": ["stun:stun.l.google.com:19302"]}
    ]

    urls = [
        value.strip()
        for value in os.getenv("TURN_URLS", "").split(",")
        if value.strip()
    ]
    if urls:
        servers.append(
            {
                "urls": urls,
                "username": os.getenv(
                    "TURN_USERNAME",
                    "",
                ),
                "credential": os.getenv(
                    "TURN_CREDENTIAL",
                    "",
                ),
            }
        )
    return servers


def _authorized(headers) -> bool:
    expected = os.getenv("OMNICAM_API_TOKEN", "")
    supplied = headers.get("authorization", "")
    return bool(expected) and supplied == f"Bearer {expected}"


class FaceSwapRuntime:
    def __init__(self) -> None:
        self.analysis = None
        self.swapper = None

    def load(self) -> None:
        from insightface.app import FaceAnalysis
        from insightface.model_zoo import get_model

        analysis_root = "/models/insightface"
        os.makedirs(analysis_root, exist_ok=True)

        self.analysis = FaceAnalysis(
            name="buffalo_l",
            root=analysis_root,
            providers=[
                "CUDAExecutionProvider",
                "CPUExecutionProvider",
            ],
        )
        self.analysis.prepare(
            ctx_id=0,
            det_size=(320, 320),
        )

        if os.path.exists(MODEL_PATH):
            self.swapper = get_model(
                MODEL_PATH,
                providers=[
                    "CUDAExecutionProvider",
                    "CPUExecutionProvider",
                ],
            )

    def source_face(self, identity_bgr):
        faces = self.analysis.get(identity_bgr)
        if not faces:
            raise ValueError(
                "No face detected in identity image"
            )

        return max(
            faces,
            key=lambda face: float(
                (face.bbox[2] - face.bbox[0])
                * (face.bbox[3] - face.bbox[1])
            ),
        )

    def transform(
        self,
        image_bgr,
        source_face,
    ):
        import cv2

        faces = self.analysis.get(image_bgr)
        result = image_bgr

        if self.swapper is not None and faces:
            target = max(
                faces,
                key=lambda face: float(
                    (face.bbox[2] - face.bbox[0])
                    * (face.bbox[3] - face.bbox[1])
                ),
            )
            result = self.swapper.get(
                image_bgr,
                target,
                source_face,
                paste_back=True,
            )

        # Permanent disclosure marker for the MVP.
        cv2.rectangle(
            result,
            (8, 8),
            (54, 31),
            (0, 0, 0),
            -1,
        )
        cv2.putText(
            result,
            "AI",
            (17, 26),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )
        return result


@app.cls(
    image=gpu_image,
    gpu="L4",
    volumes={"/models": models},
    timeout=4 * 60 * 60,
)
@modal.concurrent(
    target_inputs=1,
    max_inputs=1,
)
class GpuPeer:
    @modal.enter()
    def load(self):
        self.runtime = FaceSwapRuntime()
        self.runtime.load()

    @modal.method()
    async def run(
        self,
        state: modal.Dict,
    ):
        import cv2
        import numpy as np
        from pipecat.frames.frames import (
            InputImageRawFrame,
            OutputImageRawFrame,
        )
        from pipecat.pipeline.pipeline import Pipeline
        from pipecat.pipeline.worker import PipelineWorker
        from pipecat.processors.frame_processor import (
            FrameDirection,
            FrameProcessor,
        )
        from pipecat.transports.base_transport import (
            TransportParams,
        )
        from pipecat.transports.smallwebrtc.connection import (
            IceServer,
            SmallWebRTCConnection,
        )
        from pipecat.transports.smallwebrtc.transport import (
            SmallWebRTCTransport,
        )
        from pipecat.workers.runner import WorkerRunner

        offer = await state.get.aio("offer")
        identity_b64 = await state.get.aio(
            "identity_b64"
        )
        ice_data = await state.get.aio("ice_servers")

        identity_raw = base64.b64decode(
            identity_b64,
            validate=True,
        )
        if len(identity_raw) > MAX_IDENTITY_BYTES:
            raise ValueError(
                "identity image too large"
            )

        identity = cv2.imdecode(
            np.frombuffer(
                identity_raw,
                dtype=np.uint8,
            ),
            cv2.IMREAD_COLOR,
        )
        if identity is None:
            raise ValueError(
                "invalid identity image"
            )

        source_face = self.runtime.source_face(
            identity
        )
        runtime = self.runtime

        class SwapProcessor(FrameProcessor):
            async def process_frame(
                self,
                frame,
                direction: FrameDirection,
            ):
                await super().process_frame(
                    frame,
                    direction,
                )

                if not isinstance(
                    frame,
                    InputImageRawFrame,
                ):
                    await self.push_frame(
                        frame,
                        direction,
                    )
                    return

                width, height = frame.size
                rgb = np.frombuffer(
                    frame.image,
                    dtype=np.uint8,
                ).reshape(
                    (height, width, 3)
                )

                if frame.format == "RGB":
                    bgr = cv2.cvtColor(
                        rgb,
                        cv2.COLOR_RGB2BGR,
                    )
                else:
                    bgr = rgb

                out = runtime.transform(
                    bgr,
                    source_face,
                )
                out = cv2.cvtColor(
                    out,
                    cv2.COLOR_BGR2RGB,
                )

                await self.push_frame(
                    OutputImageRawFrame(
                        image=out.tobytes(),
                        size=(width, height),
                        format="RGB",
                    )
                )

        ice = [
            IceServer(**item)
            for item in ice_data
        ]
        connection = SmallWebRTCConnection(
            ice
        )
        await connection.initialize(
            sdp=offer["sdp"],
            type=offer["type"],
        )

        transport = SmallWebRTCTransport(
            webrtc_connection=connection,
            params=TransportParams(
                audio_in_enabled=False,
                audio_out_enabled=False,
                video_in_enabled=True,
                video_out_enabled=True,
                video_out_is_live=True,
                video_out_width=VIDEO_WIDTH,
                video_out_height=VIDEO_HEIGHT,
            ),
        )

        pipeline = Pipeline(
            [
                transport.input(),
                SwapProcessor(),
                transport.output(),
            ]
        )

        answer = connection.get_answer()
        if answer is None:
            raise RuntimeError(
                "no SDP answer"
            )
        await state.put.aio(
            "answer",
            answer,
        )

        worker = PipelineWorker(pipeline)
        runner = WorkerRunner(
            handle_sigint=False
        )
        await runner.add_workers(worker)
        await runner.run()


@app.cls(
    image=server_image,
    secrets=[config_secret],
    timeout=15 * 60,
)
class Signaling:
    @modal.asgi_app()
    def web(self):
        from fastapi import (
            FastAPI,
            HTTPException,
            Request,
        )

        web = FastAPI(
            title="OmniCam Live signaling"
        )

        @web.get("/health")
        async def health():
            return {
                "ok": True,
                "app": APP_NAME,
            }

        @web.get("/ice-servers")
        async def ice_servers(
            request: Request,
        ):
            if not _authorized(
                request.headers
            ):
                raise HTTPException(
                    status_code=401,
                    detail="unauthorized",
                )
            return {
                "ice_servers": _ice_servers()
            }

        @web.post("/offer")
        async def offer(
            request: Request,
        ):
            if not _authorized(
                request.headers
            ):
                raise HTTPException(
                    status_code=401,
                    detail="unauthorized",
                )

            body = await request.json()
            sdp = body.get("sdp")
            kind = body.get("type")
            identity_b64 = body.get(
                "identity_b64"
            )

            if not sdp or not kind or not identity_b64:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "sdp, type and "
                        "identity_b64 are required"
                    ),
                )

            ice = _ice_servers()

            async with modal.Dict.ephemeral() as state:
                await state.put.aio(
                    "offer",
                    {
                        "sdp": sdp,
                        "type": kind,
                    },
                )
                await state.put.aio(
                    "identity_b64",
                    identity_b64,
                )
                await state.put.aio(
                    "ice_servers",
                    ice,
                )

                call = await GpuPeer().run.spawn.aio(
                    state
                )
                deadline = (
                    time.monotonic()
                    + ANSWER_TIMEOUT
                )

                try:
                    while True:
                        if await request.is_disconnected():
                            raise HTTPException(
                                status_code=499,
                                detail=(
                                    "client "
                                    "disconnected"
                                ),
                            )

                        answer = await state.get.aio(
                            "answer"
                        )
                        if answer is not None:
                            return answer

                        try:
                            await call.get.aio(
                                timeout=0
                            )
                        except TimeoutError:
                            pass
                        except Exception as exc:
                            answer = await state.get.aio(
                                "answer"
                            )
                            if answer is not None:
                                return answer
                            raise HTTPException(
                                status_code=502,
                                detail=(
                                    "GPU peer "
                                    f"failed: {exc}"
                                ),
                            ) from exc
                        else:
                            answer = await state.get.aio(
                                "answer"
                            )
                            if answer is not None:
                                return answer
                            raise HTTPException(
                                status_code=502,
                                detail=(
                                    "GPU peer "
                                    "ended before "
                                    "SDP answer"
                                ),
                            )

                        if (
                            time.monotonic()
                            >= deadline
                        ):
                            raise HTTPException(
                                status_code=504,
                                detail=(
                                    "timed out "
                                    "waiting for "
                                    "SDP answer"
                                ),
                            )
                        await asyncio.sleep(0.1)
                except BaseException:
                    await call.cancel.aio()
                    raise

        return web
