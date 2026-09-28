from pathlib import Path

import numpy as np
import pytest

from omnilive.regions import Region
from omnilive.vision import (
    FallbackRegionDetector,
    SemanticLandmarks,
    align_face,
    YoloPersonDetector,
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


def test_scrfd_output_grouping_ignores_landmark_outputs():
    from omnilive.vision import ScrfdFaceDetector

    scores = [np.zeros((8, 1), dtype=np.float32) for _ in range(3)]
    boxes = [np.zeros((8, 4), dtype=np.float32) for _ in range(3)]
    landmarks = [np.zeros((8, 10), dtype=np.float32) for _ in range(3)]
    pairs = ScrfdFaceDetector._output_pairs(scores + boxes + landmarks)
    assert len(pairs) == 3
    assert all(pair[0].shape[-1] == 1 for pair in pairs)
    assert all(pair[1].shape[-1] == 4 for pair in pairs)


def test_flat_68x3_landmarks_are_not_misread_as_102x2():
    from omnilive.vision import OnnxFaceLandmarkProvider

    points = OnnxFaceLandmarkProvider._points(np.zeros(68 * 3, dtype=np.float32))
    assert points is not None
    assert points.shape == (68, 3)


def test_yolo_prediction_rows_transpose_feature_first_output():
    output = np.zeros((1, 84, 5), dtype=np.float32)
    rows = YoloPersonDetector._prediction_rows(output)
    assert rows.shape == (5, 84)


def test_yolo_person_decoder_supports_ultralytics_raw_output():
    detector = YoloPersonDetector("unused.onnx", confidence=0.4, max_regions=2)
    row = np.zeros(84, dtype=np.float32)
    row[0:4] = [320.0, 320.0, 200.0, 300.0]
    row[4] = 0.9
    regions = detector._decode_rows(
        row.reshape(1, -1),
        frame_width=640,
        frame_height=640,
        scale=1.0,
        pad_x=0,
        pad_y=0,
    )
    assert len(regions) == 1
    assert regions[0].x == 220
    assert regions[0].y == 170
    assert regions[0].width == 200
    assert regions[0].height == 300


def test_yolo_person_decoder_supports_end_to_end_output():
    detector = YoloPersonDetector("unused.onnx", confidence=0.4, max_regions=2)
    rows = np.array([
        [100.0, 120.0, 300.0, 500.0, 0.85, 0.0],
        [120.0, 130.0, 280.0, 480.0, 0.95, 2.0],
    ], dtype=np.float32)
    regions = detector._decode_rows(
        rows,
        frame_width=640,
        frame_height=640,
        scale=1.0,
        pad_x=0,
        pad_y=0,
    )
    assert len(regions) == 1
    assert regions[0].score == pytest.approx(0.85)
