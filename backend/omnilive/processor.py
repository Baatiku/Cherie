from __future__ import annotations
import time
from typing import Protocol
from av import VideoFrame
from .telemetry import processor_window

class VideoProcessor(Protocol):
    async def process(self,frame:VideoFrame)->VideoFrame: ...

class PassthroughProcessor:
    async def process(self,frame:VideoFrame)->VideoFrame:
        return frame

class TimedProcessor:
    def __init__(self,delegate:VideoProcessor|None=None):
        self.delegate=delegate or PassthroughProcessor()

    async def process(self,frame:VideoFrame)->VideoFrame:
        started=time.perf_counter()
        try:
            return await self.delegate.process(frame)
        finally:
            processor_window.add((time.perf_counter()-started)*1000.0)

processor=TimedProcessor()
