import asyncio,time
import numpy as np
from av import VideoFrame
from fastapi import FastAPI,Header,HTTPException
from pydantic import BaseModel,Field

from .config import settings
from .frame_policy import decide_frame
from .model_runtime import runtime
from .models import (
    CreateSessionRequest,EffectProfile,FrameDecision,FrameMeta,SessionResponse,
    WebRtcAnswer,WebRtcOffer,IceConfig,IceServer
)
from .processor import processor
from .session import registry
from .telemetry import media_wait_window,processor_window
from .webrtc import accept_offer,close_all_peers

app=FastAPI(title="OmniLive",version="0.7.0")

class BenchmarkRequest(BaseModel):
    effect:EffectProfile=EffectProfile.ORIGINAL
    width:int=Field(default=640,ge=64,le=1280)
    height:int=Field(default=360,ge=64,le=720)
    frames:int=Field(default=20,ge=1,le=120)

def telemetry_payload():
    media=media_wait_window.snapshot()
    proc=processor_window.snapshot()
    return {
        "media_wait_p50_ms":media.p50_ms,
        "media_wait_p95_ms":media.p95_ms,
        "media_samples":media.count,
        "processor_p50_ms":proc.p50_ms,
        "processor_p95_ms":proc.p95_ms,
        "processor_samples":proc.count,
        **runtime.status(),
    }

@app.get("/health")
def health():
    return {"ok":True,"service":"omnilive","time":int(time.time()),**telemetry_payload()}

@app.get("/v1/model")
def model_status():
    return runtime.status()

@app.get("/v1/telemetry")
def telemetry():
    return telemetry_payload()

@app.get("/v1/ice",response_model=IceConfig)
def ice_config():
    return IceConfig(servers=[IceServer(
        urls=list(settings.ice_urls),
        username=settings.ice_username,
        credential=settings.ice_credential,
    )])

@app.post("/v1/benchmark")
async def benchmark(body:BenchmarkRequest):
    before=processor_window.snapshot().count
    sample=np.zeros((body.height,body.width,3),dtype=np.uint8)
    started=time.perf_counter()
    for _ in range(body.frames):
        frame=VideoFrame.from_ndarray(sample,format="rgb24")
        await processor.process(frame,body.effect)
    elapsed_ms=(time.perf_counter()-started)*1000.0
    snap=processor_window.snapshot()
    return {
        "effect":body.effect,
        "frames":body.frames,
        "width":body.width,
        "height":body.height,
        "elapsed_ms":elapsed_ms,
        "average_ms":elapsed_ms/body.frames,
        "processor_samples_added":snap.count-before,
        "processor_p50_ms":snap.p50_ms,
        "processor_p95_ms":snap.p95_ms,
    }

@app.post("/v1/sessions",response_model=SessionResponse)
def create_session(body:CreateSessionRequest):
    return registry.create(body.profile,body.effect,body.region_hint)

@app.post("/v1/webrtc/offer",response_model=WebRtcAnswer)
async def webrtc_offer(body:WebRtcOffer):
    try:
        return await accept_offer(body)
    except PermissionError as exc:
        raise HTTPException(status_code=401,detail=str(exc)) from exc

@app.post("/v1/frames/decision",response_model=FrameDecision)
def frame_decision(body:FrameMeta,authorization:str|None=Header(default=None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401,detail="missing bearer token")
    token=authorization.removeprefix("Bearer ").strip()
    state=registry.authenticate(body.session_id,token)
    if state is None:
        raise HTTPException(status_code=401,detail="invalid or expired session")
    if body.sequence<=state.last_sequence:
        return FrameDecision(accept=False,age_ms=0,reason="out_of_order")
    decision=decide_frame(body)
    if decision.accept:
        state.last_sequence=body.sequence
    return decision

@app.on_event("shutdown")
async def shutdown_event():
    await close_all_peers()
