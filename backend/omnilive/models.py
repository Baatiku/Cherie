from enum import Enum
from pydantic import BaseModel, Field

class QualityProfile(str, Enum):
    LOW_LATENCY = "low_latency"
    BALANCED = "balanced"
    HIGH = "high"

class CreateSessionRequest(BaseModel):
    profile: QualityProfile = QualityProfile.BALANCED
    region_hint: str | None = None

class SessionResponse(BaseModel):
    session_id: str
    token: str
    profile: QualityProfile
    expires_at: int
    region_hint: str | None = None

class IceServer(BaseModel):
    urls: list[str]
    username: str = ""
    credential: str = ""

class IceConfig(BaseModel):
    servers: list[IceServer]

class FrameMeta(BaseModel):
    session_id: str
    sequence: int = Field(ge=0)
    captured_at_ms: int
    width: int = Field(gt=0, le=3840)
    height: int = Field(gt=0, le=2160)

class FrameDecision(BaseModel):
    accept: bool
    age_ms: int
    reason: str

class WebRtcOffer(BaseModel):
    session_id: str
    token: str
    sdp: str
    type: str = "offer"

class WebRtcAnswer(BaseModel):
    sdp: str
    type: str = "answer"
