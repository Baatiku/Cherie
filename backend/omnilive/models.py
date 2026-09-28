from enum import Enum
from pydantic import BaseModel,Field

class QualityProfile(str,Enum):
    LOW_LATENCY="low_latency"
    BALANCED="balanced"
    HIGH="high"

class EffectProfile(str,Enum):
    ORIGINAL="original"
    SOFT_FOCUS="soft_focus"
    MONOCHROME="monochrome"
    PIXELATE="pixelate"
    CUSTOM_ONNX="custom_onnx"

class CreateSessionRequest(BaseModel):
    profile:QualityProfile=QualityProfile.BALANCED
    effect:EffectProfile=EffectProfile.ORIGINAL
    region_hint:str|None=None

class SessionResponse(BaseModel):
    session_id:str
    token:str
    profile:QualityProfile
    effect:EffectProfile
    expires_at:int
    region_hint:str|None=None

class IceServer(BaseModel):
    urls:list[str]
    username:str=""
    credential:str=""

class IceConfig(BaseModel):
    servers:list[IceServer]
    credential_mode:str="static"
    expires_at:int|None=None

class FrameMeta(BaseModel):
    session_id:str
    sequence:int=Field(ge=0)
    captured_at_ms:int
    width:int=Field(gt=0,le=3840)
    height:int=Field(gt=0,le=2160)

class FrameDecision(BaseModel):
    accept:bool
    age_ms:int
    reason:str

class WebRtcOffer(BaseModel):
    session_id:str
    token:str
    sdp:str
    type:str="offer"

class WebRtcAnswer(BaseModel):
    sdp:str
    type:str="answer"
