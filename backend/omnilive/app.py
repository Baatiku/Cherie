import asyncio
import time
import uuid

import numpy as np
from av import VideoFrame
from fastapi import FastAPI, Header, HTTPException, Response, WebSocket, WebSocketDisconnect
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
from .processor import ProcessingTrace, processor
from .regions import Region
from .session import registry
from .temporal import profile_defaults
from .telemetry import (
    cache_reuse_counter,
    compositor_window,
    detector_window,
    dropped_inference_counter,
    frame_correlation_drop_counter,
    frame_correlation_match_counter,
    frame_correlation_out_of_order_counter,
    frame_timing_publish_drop_counter,
    inference_error_counter,
    input_age_window,
    input_overwrite_counter,
    landmark_window,
    media_wait_window,
    model_window,
    overload_event_counter,
    pipeline_health,
    processor_window,
    tracker_window,
    tracking_reset_counter,
)
from .webrtc import FrameTimingBridge, accept_offer, close_all_peers, close_peer

app = FastAPI(title="OmniLive", version="0.10.0")


class BenchmarkRequest(BaseModel):
    effect: EffectProfile = EffectProfile.ORIGINAL
    profile: QualityProfile = QualityProfile.BALANCED
    width: int = Field(default=640, ge=64, le=1280)
    height: int = Field(default=360, ge=64, le=720)
    frames: int = Field(default=20, ge=1, le=120)
    force_region: bool = True
    respect_inference_budget: bool = False


class BenchmarkMatrixRequest(BaseModel):
    effect: EffectProfile = EffectProfile.CUSTOM_ONNX
    profiles: list[QualityProfile] = Field(
        default_factory=lambda: [
            QualityProfile.LOW_LATENCY,
            QualityProfile.BALANCED,
            QualityProfile.HIGH,
        ],
        min_length=1,
        max_length=3,
    )
    crop_sizes: list[int] = Field(default_factory=lambda: [256, 320, 384, 448], min_length=1, max_length=8)
    width: int = Field(default=960, ge=128, le=1280)
    height: int = Field(default=540, ge=128, le=720)
    warmup_frames: int = Field(default=2, ge=0, le=20)
    steady_frames: int = Field(default=30, ge=3, le=120)
    force_region: bool = True
    include_adaptation_probe: bool = True
    adaptation_frames: int = Field(default=24, ge=4, le=120)


class LiveSocketStart(BaseModel):
    action: str = "start"
    profile: QualityProfile = QualityProfile.BALANCED
    effect: EffectProfile = EffectProfile.ORIGINAL
    region_hint: str | None = None
    sdp: str = Field(min_length=1)
    type: str = "offer"


class LiveFrameMark(BaseModel):
    action: str = "frame_mark"
    sequence: int = Field(ge=0)
    capture_timestamp_ns: int = Field(ge=0)
    capture_elapsed_ns: int = Field(ge=0)
    rtp_timestamp: int = Field(ge=0, le=0xFFFFFFFF)


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * q)))
    return ordered[index]


async def _benchmark_case(
    *,
    effect: EffectProfile,
    profile: QualityProfile,
    crop_max_side: int,
    width: int,
    height: int,
    warmup_frames: int,
    steady_frames: int,
    force_region: bool,
) -> dict:
    sample = np.zeros((height, width, 3), dtype=np.uint8)
    sample[::8, :, :] = 96
    sample[:, ::8, :] = 160
    stream_key = "benchmark-matrix-" + uuid.uuid4().hex
    forced = Region(0, 0, width, height) if force_region else None
    runtime_loaded_before = runtime.loaded
    memory_before = runtime.memory_status()
    traces: list[ProcessingTrace] = []

    async def run_frame() -> ProcessingTrace:
        trace = ProcessingTrace()
        frame = VideoFrame.from_ndarray(sample, format="rgb24")
        await processor.process(
            frame,
            effect,
            stream_key=stream_key,
            profile=profile,
            force_region=forced,
            force_inference=True,
            crop_max_side_override=crop_max_side,
            trace=trace,
        )
        traces.append(trace)
        return trace

    try:
        first = await run_frame()
        warmup: list[ProcessingTrace] = []
        for _ in range(warmup_frames):
            warmup.append(await run_frame())
        steady: list[ProcessingTrace] = []
        for _ in range(steady_frames):
            steady.append(await run_frame())
    finally:
        processor.release_stream(stream_key)

    memory_after = runtime.memory_status()
    steady_model = [trace.model_ms for trace in steady if trace.model_ms is not None]
    steady_total = [trace.total_ms for trace in steady]
    model_p50 = _percentile(steady_model, 0.50)
    model_p95 = _percentile(steady_model, 0.95)
    total_p50 = _percentile(steady_total, 0.50)
    total_p95 = _percentile(steady_total, 0.95)
    last = steady[-1] if steady else first
    model_inputs = [
        (trace.model_input_width, trace.model_input_height)
        for trace in steady
        if trace.model_input_width > 0 and trace.model_input_height > 0
    ]
    actual_input = model_inputs[-1] if model_inputs else (first.model_input_width, first.model_input_height)

    return {
        "profile": profile.value,
        "effect": effect.value,
        "requested_crop_max_side": crop_max_side,
        "model_input_width": actual_input[0],
        "model_input_height": actual_input[1],
        "runtime_loaded_before": runtime_loaded_before,
        "runtime_loaded_after": runtime.loaded,
        "runtime_load_ms": runtime.load_ms,
        "first_pipeline_ms": round(first.total_ms, 3),
        "first_model_ms": None if first.model_ms is None else round(first.model_ms, 3),
        "warmup_frames": warmup_frames,
        "warmup_pipeline_ms": [round(trace.total_ms, 3) for trace in warmup],
        "steady_frames": steady_frames,
        "steady_model_p50_ms": round(model_p50, 3),
        "steady_model_p95_ms": round(model_p95, 3),
        "steady_pipeline_p50_ms": round(total_p50, 3),
        "steady_pipeline_p95_ms": round(total_p95, 3),
        "sustainable_neural_fps_at_p95": round(1000.0 / model_p95, 2) if model_p95 > 0 else 0.0,
        "sustainable_pipeline_fps_at_p95": round(1000.0 / total_p95, 2) if total_p95 > 0 else 0.0,
        "inference_attempts": sum(1 for trace in traces if trace.inference_attempted),
        "inference_successes": sum(1 for trace in traces if trace.inference_succeeded),
        "inference_errors": sum(1 for trace in traces if trace.inference_error),
        "final_target_neural_fps": round(last.target_neural_fps, 2),
        "final_detector_interval": last.detector_interval,
        "final_crop_max_side": last.crop_max_side,
        "overload_events": last.overload_events,
        "memory_before": memory_before,
        "memory_after": memory_after,
    }


async def _adaptation_probe(
    *,
    effect: EffectProfile,
    profile: QualityProfile,
    width: int,
    height: int,
    frames: int,
    force_region: bool,
) -> dict:
    sample = np.zeros((height, width, 3), dtype=np.uint8)
    sample[::8, :, :] = 96
    sample[:, ::8, :] = 160
    stream_key = "benchmark-adapt-" + uuid.uuid4().hex
    forced = Region(0, 0, width, height) if force_region else None
    defaults = profile_defaults(profile.value)
    traces: list[ProcessingTrace] = []

    try:
        for _ in range(frames):
            trace = ProcessingTrace()
            frame = VideoFrame.from_ndarray(sample, format="rgb24")
            await processor.process(
                frame,
                effect,
                stream_key=stream_key,
                profile=profile,
                force_region=forced,
                force_inference=True,
                trace=trace,
            )
            traces.append(trace)
    finally:
        processor.release_stream(stream_key)

    final = traces[-1]
    model_values = [trace.model_ms for trace in traces if trace.model_ms is not None]
    return {
        "profile": profile.value,
        "frames": frames,
        "initial_target_neural_fps": defaults.neural_fps,
        "initial_detector_interval": defaults.detector_interval,
        "initial_crop_max_side": defaults.crop_max_side,
        "final_target_neural_fps": round(final.target_neural_fps, 2),
        "final_detector_interval": final.detector_interval,
        "final_crop_max_side": final.crop_max_side,
        "overload_events": final.overload_events,
        "model_p95_ms": round(_percentile(model_values, 0.95), 3),
        "inference_errors": sum(1 for trace in traces if trace.inference_error),
        "shed_neural_fps": final.target_neural_fps < defaults.neural_fps,
        "shed_detector_cadence": final.detector_interval > defaults.detector_interval,
        "shed_crop": final.crop_max_side < defaults.crop_max_side,
    }


def _window_payload(window) -> dict:
    snap = window.snapshot()
    return {"count": snap.count, "p50_ms": snap.p50_ms, "p95_ms": snap.p95_ms}


def telemetry_payload():
    media = media_wait_window.snapshot()
    proc = processor_window.snapshot()
    detect = detector_window.snapshot()
    track = tracker_window.snapshot()
    model = model_window.snapshot()
    landmark = landmark_window.snapshot()
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
        "landmark_p50_ms": landmark.p50_ms,
        "landmark_p95_ms": landmark.p95_ms,
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
        "frame_correlations": frame_correlation_match_counter.value,
        "frame_correlation_drops": frame_correlation_drop_counter.value,
        "frame_correlation_out_of_order": frame_correlation_out_of_order_counter.value,
        "frame_timing_publish_drops": frame_timing_publish_drop_counter.value,
        "stages": {
            "input_wait": _window_payload(media_wait_window),
            "input_age": _window_payload(input_age_window),
            "detect": _window_payload(detector_window),
            "track": _window_payload(tracker_window),
            "landmark": _window_payload(landmark_window),
            "model": _window_payload(model_window),
            "composite": _window_payload(compositor_window),
            "total": _window_payload(processor_window),
        },
        **pipeline_health.snapshot(),
        **processor.vision_status(),
        **runtime.status(),
    }


def _health_state(payload: dict) -> tuple[bool, str]:
    if payload.get("configured") and not payload.get("loaded"):
        return False, "model_unavailable"
    return True, "ok"


def _metric_line(name: str, value, labels: dict[str, str] | None = None) -> str:
    label_text = ""
    if labels:
        escaped = []
        for key, raw in labels.items():
            value_text = str(raw).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
            escaped.append(f'{key}="{value_text}"')
        label_text = "{" + ",".join(escaped) + "}"
    return f"{name}{label_text} {value}"


def prometheus_payload() -> str:
    payload = telemetry_payload()
    memory = runtime.memory_status()
    lines = [
        "# TYPE omnilive_processor_latency_ms gauge",
        _metric_line("omnilive_processor_latency_ms", payload["processor_p50_ms"], {"quantile": "0.50"}),
        _metric_line("omnilive_processor_latency_ms", payload["processor_p95_ms"], {"quantile": "0.95"}),
        "# TYPE omnilive_model_latency_ms gauge",
        _metric_line("omnilive_model_latency_ms", payload["model_p50_ms"], {"quantile": "0.50"}),
        _metric_line("omnilive_model_latency_ms", payload["model_p95_ms"], {"quantile": "0.95"}),
        "# TYPE omnilive_input_age_ms gauge",
        _metric_line("omnilive_input_age_ms", payload["input_age_p50_ms"], {"quantile": "0.50"}),
        _metric_line("omnilive_input_age_ms", payload["input_age_p95_ms"], {"quantile": "0.95"}),
        "# TYPE omnilive_input_overwritten_frames_total counter",
        _metric_line("omnilive_input_overwritten_frames_total", payload["input_overwritten_frames"]),
        "# TYPE omnilive_dropped_inference_frames_total counter",
        _metric_line("omnilive_dropped_inference_frames_total", payload["dropped_inference_frames"]),
        "# TYPE omnilive_inference_errors_total counter",
        _metric_line("omnilive_inference_errors_total", payload["inference_errors"]),
        "# TYPE omnilive_overload_events_total counter",
        _metric_line("omnilive_overload_events_total", payload["overload_events"]),
        "# TYPE omnilive_frame_correlations_total counter",
        _metric_line("omnilive_frame_correlations_total", payload["frame_correlations"]),
        "# TYPE omnilive_frame_correlation_drops_total counter",
        _metric_line("omnilive_frame_correlation_drops_total", payload["frame_correlation_drops"]),
        "# TYPE omnilive_active_processor_streams gauge",
        _metric_line("omnilive_active_processor_streams", payload["active_processor_streams"]),
        "# TYPE omnilive_target_neural_fps gauge",
        _metric_line("omnilive_target_neural_fps", payload["target_neural_fps"]),
        "# TYPE omnilive_crop_max_side gauge",
        _metric_line("omnilive_crop_max_side", payload["crop_max_side"]),
        "# TYPE omnilive_model_loaded gauge",
        _metric_line("omnilive_model_loaded", 1 if payload.get("loaded") else 0),
    ]
    if memory.get("process_rss_mb") is not None:
        lines.extend([
            "# TYPE omnilive_process_rss_mb gauge",
            _metric_line("omnilive_process_rss_mb", memory["process_rss_mb"]),
        ])
    if memory.get("gpu_used_mb") is not None:
        lines.extend([
            "# TYPE omnilive_gpu_memory_used_mb gauge",
            _metric_line("omnilive_gpu_memory_used_mb", memory["gpu_used_mb"]),
        ])
    for provider in payload.get("providers") or []:
        lines.append(_metric_line("omnilive_model_provider_info", 1, {"provider": provider}))
    return "\n".join(lines) + "\n"


@app.get("/health")
def health():
    payload = telemetry_payload()
    ok, state = _health_state(payload)
    return {"ok": ok, "status": state, "service": "omnilive", "time": int(time.time()), **payload}


@app.get("/metrics")
def metrics():
    return Response(content=prometheus_payload(), media_type="text/plain; version=0.0.4")


@app.get("/v1/model")
def model_status():
    return {
        **runtime.status(),
        "vision": processor.vision_status(),
        "memory": runtime.memory_status(),
    }


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
    timing_task = None
    telemetry_task = None
    send_lock = asyncio.Lock()
    timing_queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=64)

    async def send_json(payload: dict) -> None:
        async with send_lock:
            await websocket.send_json(payload)

    def publish_timing(report: dict) -> None:
        payload = {"event": "frame_timing", **report}
        if timing_queue.full():
            try:
                timing_queue.get_nowait()
                frame_timing_publish_drop_counter.increment()
            except asyncio.QueueEmpty:
                pass
        try:
            timing_queue.put_nowait(payload)
        except asyncio.QueueFull:
            frame_timing_publish_drop_counter.increment()

    async def timing_sender() -> None:
        while True:
            await send_json(await timing_queue.get())

    async def telemetry_sender() -> None:
        while True:
            await asyncio.sleep(2.0)
            await send_json({"event": "telemetry", "telemetry": telemetry_payload()})

    timing_bridge = FrameTimingBridge(publish_timing)
    try:
        payload = await websocket.receive_json()
        if payload.get("action") != "start":
            await send_json({"event": "error", "detail": "first message must be start"})
            return
        try:
            start = LiveSocketStart.model_validate(payload)
        except ValidationError:
            await send_json({"event": "error", "detail": "invalid live start payload"})
            return

        session = registry.create(start.profile, start.effect, start.region_hint)
        answer = await accept_offer(
            WebRtcOffer(
                session_id=session.session_id,
                token=session.token,
                sdp=start.sdp,
                type=start.type,
            ),
            timing_bridge=timing_bridge,
        )
        await send_json(
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

        timing_task = asyncio.create_task(timing_sender(), name="omnilive-frame-timing")
        telemetry_task = asyncio.create_task(telemetry_sender(), name="omnilive-live-telemetry")

        while True:
            message = await websocket.receive_json()
            action = message.get("action")
            if action == "stop":
                break
            if action == "ping":
                await send_json({"event": "pong", "telemetry": telemetry_payload()})
                continue
            if action == "profile":
                try:
                    profile = QualityProfile(message.get("profile"))
                except (TypeError, ValueError):
                    await send_json({"event": "error", "detail": "invalid quality profile"})
                    continue
                if registry.update_profile(session.session_id, profile):
                    await send_json({"event": "profile", "profile": profile.value})
                continue
            if action == "frame_mark":
                try:
                    mark = LiveFrameMark.model_validate(message)
                except ValidationError:
                    await send_json({"event": "error", "detail": "invalid frame mark"})
                    continue
                timing_bridge.note_capture(
                    mark.sequence,
                    mark.capture_timestamp_ns,
                    mark.capture_elapsed_ns,
                    mark.rtp_timestamp,
                )
                continue
            await send_json({"event": "error", "detail": "unsupported live action"})
    except WebSocketDisconnect:
        pass
    except Exception:
        try:
            await send_json({"event": "error", "detail": "live session failed"})
        except Exception:
            pass
    finally:
        tasks = [task for task in (timing_task, telemetry_task) if task is not None]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
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


@app.post("/v1/benchmark/matrix")
async def benchmark_matrix(body: BenchmarkMatrixRequest):
    crop_sizes = [max(64, min(1024, int(value))) for value in body.crop_sizes]
    cases = []
    started = time.perf_counter()
    for profile in body.profiles:
        for crop_size in crop_sizes:
            cases.append(
                await _benchmark_case(
                    effect=body.effect,
                    profile=profile,
                    crop_max_side=crop_size,
                    width=body.width,
                    height=body.height,
                    warmup_frames=body.warmup_frames,
                    steady_frames=body.steady_frames,
                    force_region=body.force_region,
                )
            )
    adaptation = []
    if body.include_adaptation_probe:
        for profile in body.profiles:
            adaptation.append(
                await _adaptation_probe(
                    effect=body.effect,
                    profile=profile,
                    width=body.width,
                    height=body.height,
                    frames=body.adaptation_frames,
                    force_region=body.force_region,
                )
            )
    return {
        "effect": body.effect.value,
        "profiles": [profile.value for profile in body.profiles],
        "crop_sizes": crop_sizes,
        "case_count": len(cases),
        "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 3),
        "runtime": runtime.status(),
        "vision": processor.vision_status(),
        "memory": runtime.memory_status(),
        "cases": cases,
        "adaptation": adaptation,
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
