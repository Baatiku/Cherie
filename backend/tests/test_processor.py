import asyncio
from av import VideoFrame
from omnilive.processor import TimedProcessor
from omnilive.telemetry import processor_window

def test_processor_records_latency():
    before=processor_window.snapshot().count
    frame=VideoFrame(width=16,height=16,format="yuv420p")
    result=asyncio.run(TimedProcessor().process(frame))
    assert result is frame
    assert processor_window.snapshot().count==before+1
