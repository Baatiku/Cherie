import asyncio
import time
import uuid

import numpy as np
from av import VideoFrame
from fastapi import FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field, ValidationError

from .config import settings
from .frame_policy import decide_frame
from .ice import current_ice_config
from .model_runtime import runtime
from .models import (
    CreateSessionRequest,
    EffectProfile,
    FrameDecision,
    FrameMeta,
    IceConfig,
    QualityProfile,
    SessionResponse,
    WebRtcAnswer,
    WebRtcOffer,
)
from .processor import processor
from .regions import Region
from .session import registry
from .telemetry import (
    cache_reuse_counter,
    compositor_window,
    detector_window,
    dropped_inference_counter,
    inference_error_counter,
    input_age_window,
    input_overwrite_counter,
    media_wait_window,
    model_window,
    overload_event_counter,
    pipeline_health,
    processor_window,
    tracker_window,
    tracking_reset_counter,
)
from .webrtc import accept_offer, close_all_peers, close_peer

app = FastAPI(title="OmniLive", version="0.9.0")


class BenchmarkRequest(BaseModel):
    effect: EffectProfile = EffectProfile.ORIGINAL
    profile: QualityProfile = QualityProfile.BALANCED
    width: int = Field(default=640, ge=64, le=1280)
    height: int = Field(default=360, ge=64, le=720)
    frames: int = Field(default=20, ge=1, le=120)
    force_region: bool = True
    respect_inference_budget: bool = False


class LiveSocketStart(BaseModel):
    action: str = "start"
    profile: QualityProfile = QualityProfile.BALANCED
    effect: EffectProfile = EffectProfile.ORIGINAL
    region_hint: str | None = None
    sdp: str = Field(min_length=1)
    type: str = "offer"


def _window_payload(window) -> dict:
    snap = window.snapshot()
    return {"count": snap.count, "p50_ms": snap.p50_ms, "p95_ms": snap.p95_ms}


def telemetry_payload():
    media = media_wait_window.snapshot()
    proc = processor_window.snapshot()
    detect = detector_window.snapshot()
    track = tracker_window.snapshot()
    model = model_window.snapshot()
    composite = compositor_window.snapshot()
    input_age = input_age_window.snapshot()
    return {
        "media_wait_p50_ms": media.p50_ms,
        "media_wait_p95_ms": media.p95_ms,
        "media_samples": media.count,
        "input_age_p50_ms": input_age.p50_ms,
        "input_age_p95_ms": input_age.p95_ms,
        "processor_p50_ms": proc.p50_ms,
        "processor_p95_ms": proc.p95_ms,
        "processor_samples": proc.count,
        "detector_p50_ms": detect.p50_ms,
        "detector_p95_ms": detect.p95_ms,
        "tracker_p50_ms": track.p50_ms,
        "tracker_p95_ms": track.p95_ms,
        "model_p50_ms": model.p50_ms,
        "model_p95_ms": model.p95_ms,
        "compositor_p50_ms": composite.p50_ms,
        "compositor_p95_ms": composite.p95_ms,
        "dropped_inference_frames": dropped_inference_counter.value,
        "input_overwritten_frames": input_overwrite_counter.value,
        "cache_reuse_frames": cache_reuse_counter.value,
        "tracking_resets": tracking_reset_counter.value,
        "inference_errors": inference_error_counter.value,
        "overload_events": overload_event_counter.value,
        "stages": {
            "input_wait": _window_payload(media_wait_window),
            "input_age": _window_payload(input_age_window),
            "detect": _window_payload(detector_window),
            "track": _window_payload(tracker_window),
            "model": _window_payload(model_window),
            "composite": _window_payload(compositor_window),
            "total": _window_payload(processor_window),
        },
        **pipeline_health.snapshot(),
        **runtime.status(),
    }


@app.get("/health")
def health():
    return {"ok": True, "service": "omnilive", "time": int(time.time()), **telemetry_payload()}


@app.get("/v1/model")
def model_status():
    return runtime.status()


@app.get("/v1/telemetry")
def telemetry():
    return telemetry_payload()


@app.get("/v1/ice", response_model=IceConfig)
def ice_config():
    return current_ice_config()


@app.websocket("/v1/live")
async def live_socket(websocket: WebSocket):
    await websocket.accept()
    session = None
    try:
        payload = await websocket.receive_json()
        if payload.get("action") != "start":
            await websocket.send_json({"event": "error", "detail": "first message must be start"})
            return
        try:
            start = LiveSocketStart.model_validate(payload)
        except ValidationError:
            await websocket.send_json({"event": "error", "detail": "invalid live start payload"})
            return

        session = registry.create(start.profile, start.effect, start.region_hint)
        answer = await accept_offer(
            WebRtcOffer(
                session_id=session.session_id,
                token=session.token,
                sdp=start.sdp,
                type=start.type,
            )
        )
        await websocket.send_json(
            {
                "event": "answer",
                "session_id": session.session_id,
                "token": session.token,
                "expires_at": session.expires_at,
                "profile": session.profile.value,
                "effect": session.effect.value,
                "sdp": answer.sdp,
                "type": answer.type,
            }
        )

        while int(time.time()) < session.expires_at:
            try:
                message = await asyncio.wait_for(websocket.receive_json(), timeout=2.0)
            except asyncio.TimeoutError:
                await websocket.send_json({"event": "telemetry", "telemetry": telemetry_payload()})
                continue

            action = message.get("action")
            if action == "stop":
                break
            if action == "ping":
                await websocket.send_json({"event": "pong", "telemetry": telemetry_payload()})
            else:
                await websocket.send_json({"event": "error", "detail": "unsupported live action"})

        if int(time.time()) >= session.expires_at:
            await websocket.send_json({"event": "expired"})
    except WebSocketDisconnect:
        pass
    except Exception:
        try:
            await websocket.send_json({"event": "error", "detail": "live session failed"})
        except Exception:
            pass
    finally:
        if session is not None:
            await close_peer(session.session_id)


@app.post("/v1/benchmark")
async def benchmark(body: BenchmarkRequest):
    before = processor_window.snapshot().count
    sample = np.zeros((body.height, body.width, 3), dtype=np.uint8)
    sample[::8, :, :] = 96
    sample[:, ::8, :] = 160
    stream_key = "benchmark-" + uuid.uuid4().hex
    forced = Region(0, 0, body.width, body.height) if body.force_region else None
    started = time.perf_counter()
    try:
        for _ in range(body.frames):
            frame = VideoFrame.from_ndarray(sample, format="rgb24")
            await processor.process(
                frame,
                body.effect,
                stream_key=stream_key,
                profile=body.profile,
                force_region=forced,
                force_inference=not body.respect_inference_budget,
            )
    finally:
        processor.release_stream(stream_key)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    snap = processor_window.snapshot()
    return {
        "effect": body.effect,
        "profile": body.profile,
        "frames": body.frames,
        "width": body.width,
        "height": body.height,
        "elapsed_ms": elapsed_ms,
        "average_ms": elapsed_ms / body.frames,
        "processor_samples_added": snap.count - before,
        "processor_p50_ms": snap.p50_ms,
        "processor_p95_ms": snap.p95_ms,
        "respect_inference_budget": body.respect_inference_budget,
        "telemetry": telemetry_payload(),
    }


@app.post("/v1/sessions", response_model=SessionResponse)
def create_session(body: CreateSessionRequest):
    return registry.create(body.profile, body.effect, body.region_hint)


@app.post("/v1/webrtc/offer", response_model=WebRtcAnswer)
async def webrtc_offer(body: WebRtcOffer):
    try:
        return await accept_offer(body)
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@app.post("/v1/frames/decision", response_model=FrameDecision)
def frame_decision(body: FrameMeta, authorization: str | None = Header(default=None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    state = registry.authenticate(body.session_id, token)
    if state is None:
        raise HTTPException(status_code=401, detail="invalid or expired session")
    if body.sequence <= state.last_sequence:
        return FrameDecision(accept=False, age_ms=0, reason="out_of_order")
    decision = decide_frame(body)
    if decision.accept:
        state.last_sequence = body.sequence
    return decision


@app.on_event("shutdown")
async def shutdown_event():
    await close_all_peers()
