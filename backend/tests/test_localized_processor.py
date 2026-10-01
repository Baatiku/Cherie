import asyncio

import numpy as np
from av import VideoFrame

from omnilive.models import EffectProfile, QualityProfile
from omnilive.processor import LocalizedTemporalProcessor, TimedProcessor
from omnilive.regions import Region
from omnilive.telemetry import cache_reuse_counter, compositor_window, model_window


def _textured_frame(width=96, height=72):
    rgb = np.zeros((height, width, 3), dtype=np.uint8)
    rgb[:, :] = (20, 40, 80)
    rgb[18:54, 30:66] = (210, 70, 30)
    rgb[20:54:5, 32:66:5] = 255
    return VideoFrame.from_ndarray(rgb, format="rgb24")


def test_forced_region_exercises_localized_pipeline():
    localized = LocalizedTemporalProcessor()
    processor = TimedProcessor(localized=localized)
    before_model = model_window.snapshot().count
    before_composite = compositor_window.snapshot().count
    frame = _textured_frame()
    output = asyncio.run(
        processor.process(
            frame,
            EffectProfile.CUSTOM_ONNX,
            stream_key="localized-test",
            profile=QualityProfile.BALANCED,
            force_region=Region(24, 12, 48, 48),
            force_inference=True,
        )
    )
    assert output.width == frame.width
    assert output.height == frame.height
    assert model_window.snapshot().count == before_model + 1
    assert compositor_window.snapshot().count == before_composite + 1
    processor.release_stream("localized-test")


def test_temporal_cache_reused_between_inference_slots():
    localized = LocalizedTemporalProcessor()
    processor = TimedProcessor(localized=localized)
    key = "cache-test"
    region = Region(24, 12, 48, 48)
    before = cache_reuse_counter.value
    asyncio.run(
        processor.process(
            _textured_frame(),
            EffectProfile.CUSTOM_ONNX,
            stream_key=key,
            profile=QualityProfile.LOW_LATENCY,
            force_region=region,
            force_inference=True,
        )
    )
    asyncio.run(
        processor.process(
            _textured_frame(),
            EffectProfile.CUSTOM_ONNX,
            stream_key=key,
            profile=QualityProfile.LOW_LATENCY,
            force_region=region,
            force_inference=False,
        )
    )
    assert cache_reuse_counter.value >= before + 1
    processor.release_stream(key)
