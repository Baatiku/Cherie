import base64

import cv2
import numpy as np
import pytest

from omnilive.reference_image import decode_reference_image


def _jpeg(width=160, height=120):
    image = np.zeros((height, width, 3), dtype=np.uint8)
    image[:, :, 1] = 180
    ok, encoded = cv2.imencode(".jpg", image)
    assert ok
    return encoded.tobytes()


def test_reference_image_validates_and_normalizes():
    payload = base64.b64encode(_jpeg()).decode("ascii")
    result = decode_reference_image(payload)
    assert result.width == 160
    assert result.height == 120
    assert result.mime_type == "image/jpeg"
    assert len(result.sha256) == 64
    assert result.jpeg_bytes.startswith(b"\xff\xd8")


def test_reference_image_downscales_large_input():
    payload = base64.b64encode(_jpeg(1600, 800)).decode("ascii")
    result = decode_reference_image(payload, max_side=400)
    assert result.width == 400
    assert result.height == 200


def test_reference_image_rejects_invalid_base64():
    with pytest.raises(ValueError, match="base64"):
        decode_reference_image("not:base64")
