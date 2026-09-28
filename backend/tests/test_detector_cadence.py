import numpy as np

from omnilive.models import EffectProfile, QualityProfile
from omnilive.processor import LocalizedTemporalProcessor


class EmptyDetector:
    name = "empty-test-detector"

    def __init__(self):
        self.calls = 0

    def detect(self, rgb):
        self.calls += 1
        return []


def test_empty_scene_respects_detector_cadence():
    detector = EmptyDetector()
    processor = LocalizedTemporalProcessor(detector=detector)
    frame = np.zeros((72, 96, 3), dtype=np.uint8)
    for _ in range(6):
        processor.process_rgb(
            frame,
            EffectProfile.CUSTOM_ONNX,
            stream_key="empty-scene",
            profile=QualityProfile.LOW_LATENCY,
        )
    assert detector.calls == 1

    for _ in range(6):
        processor.process_rgb(
            frame,
            EffectProfile.CUSTOM_ONNX,
            stream_key="empty-scene",
            profile=QualityProfile.LOW_LATENCY,
        )
    assert detector.calls == 1

    processor.process_rgb(
        frame,
        EffectProfile.CUSTOM_ONNX,
        stream_key="empty-scene",
        profile=QualityProfile.LOW_LATENCY,
    )
    assert detector.calls == 2
    processor.release_stream("empty-scene")
