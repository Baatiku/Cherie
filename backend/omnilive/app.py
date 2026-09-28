import time
from fastapi import FastAPI, Header, HTTPException
from .frame_policy import decide_frame
from .models import CreateSessionRequest, FrameDecision, FrameMeta, SessionResponse
from .session import registry

app = FastAPI(title="OmniLive", version="0.1.0")

@app.get("/health")
def health():
    return {"ok": True, "service": "omnilive", "time": int(time.time())}

@app.post("/v1/sessions", response_model=SessionResponse)
def create_session(body: CreateSessionRequest):
    return registry.create(body.profile, body.region_hint)

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
