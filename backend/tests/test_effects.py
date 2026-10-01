import asyncio
from av import VideoFrame
from omnilive.models import EffectProfile
from omnilive.processor import TimedProcessor

def make_frame():
    return VideoFrame(width=32,height=24,format="rgb24")

def test_original_effect_returns_frame():
    frame=make_frame()
    out=asyncio.run(TimedProcessor().process(frame,EffectProfile.ORIGINAL))
    assert out is frame

def test_custom_onnx_without_model_keeps_geometry():
    frame=make_frame()
    out=asyncio.run(TimedProcessor().process(frame,EffectProfile.CUSTOM_ONNX))
    assert out.width==32
    assert out.height==24
