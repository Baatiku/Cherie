from pathlib import Path

import numpy as np
import pytest

from omnilive.regions import Region
from omnilive.vision import (
    FallbackRegionDetector,
    SemanticLandmarks,
    align_face,
    resolve_vision_model_path,
)


def test_vision_model_path_stays_inside_model_root(tmp_path: Path):
    model = tmp_path / "detectors" / "face.onnx"
    model.parent.mkdir()
    model.write_bytes(b"placeholder")
    assert resolve_vision_model_path("detectors/face.onnx", root=tmp_path) == model.resolve()

    with pytest.raises(ValueError):
        resolve_vision_model_path("../escape.onnx", root=tmp_path, require_exists=False)


def test_detector_falls_back_only_when_primary_is_not_operational():
    class Primary:
        name = "primary"
        operational = False

        def detect(self, rgb):
            return []

    class Fallback:
        name = "fallback"

        def detect(self, rgb):
            return [Region(1, 2, 10, 12, 0.8)]

    detector = FallbackRegionDetector(Primary(), Fallback())
    result = detector.detect(np.zeros((32, 32, 3), dtype=np.uint8))
    assert result == [Region(1, 2, 10, 12, 0.8)]


def test_five_point_alignment_produces_requested_square():
    rgb = np.zeros((120, 120, 3), dtype=np.uint8)
    points = np.array(
        [[38, 45], [82, 45], [60, 65], [43, 86], [78, 86]],
        dtype=np.float32,
    )
    landmarks = SemanticLandmarks(points=points, score=0.9, schema="face_5")
    aligned = align_face(rgb, landmarks, size=96)
    assert aligned is not None
    assert aligned.image.shape == (96, 96, 3)
    assert aligned.transform.shape == (2, 3)
