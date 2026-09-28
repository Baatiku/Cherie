import time
from fastapi import FastAPI, Header, HTTPException
from .frame_policy import decide_frame
from .models import CreateSessionRequest, FrameDecision, FrameMeta, SessionResponse, WebRtcAnswer, WebRtcOffer
from .session import registry
from .webrtc import accept_offer, close_all_peers

app = FastAPI(title="OmniLive", version="0.2.0")

@app.get("/health")
def health():
    return {"ok": True, "service": "omnilive", "time": int(time.time())}

@app.post("/v1/sessions", response_model=SessionResponse)
def create_session(body: CreateSessionRequest):
    return registry.create(body.profile, body.region_hint)

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
