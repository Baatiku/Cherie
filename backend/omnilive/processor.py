from __future__ import annotations
import time
from av import VideoFrame
import cv2
import numpy as np

from .model_runtime import runtime
from .models import EffectProfile
from .telemetry import processor_window

class VisualProcessor:
    async def process(self,frame:VideoFrame,effect:EffectProfile)->VideoFrame:
        if effect==EffectProfile.ORIGINAL:
            return frame

        rgb=frame.to_ndarray(format="rgb24")

        if effect==EffectProfile.MONOCHROME:
            gray=cv2.cvtColor(rgb,cv2.COLOR_RGB2GRAY)
            out=np.repeat(gray[...,None],3,axis=2)
        elif effect==EffectProfile.SOFT_FOCUS:
            blurred=cv2.GaussianBlur(rgb,(0,0),sigmaX=2.0,sigmaY=2.0)
            out=cv2.addWeighted(rgb,0.72,blurred,0.28,0)
        elif effect==EffectProfile.PIXELATE:
            h,w=rgb.shape[:2]
            sw=max(16,w//18)
            sh=max(16,h//18)
            small=cv2.resize(rgb,(sw,sh),interpolation=cv2.INTER_LINEAR)
            out=cv2.resize(small,(w,h),interpolation=cv2.INTER_NEAREST)
        elif effect==EffectProfile.CUSTOM_ONNX:
            out=runtime.run_image(rgb)
        else:
            out=rgb

        result=VideoFrame.from_ndarray(out,format="rgb24")
        result.pts=frame.pts
        result.time_base=frame.time_base
        return result

class TimedProcessor:
    def __init__(self):
        self.delegate=VisualProcessor()

    async def process(self,frame:VideoFrame,effect:EffectProfile)->VideoFrame:
        started=time.perf_counter()
        try:
            return await self.delegate.process(frame,effect)
        finally:
            processor_window.add((time.perf_counter()-started)*1000.0)

processor=TimedProcessor()
