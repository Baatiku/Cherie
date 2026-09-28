from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class ValidatedReferenceImage:
    jpeg_bytes: bytes
    width: int
    height: int
    sha256: str
    mime_type: str = "image/jpeg"


def decode_reference_image(
    data_base64: str,
    *,
    max_payload_bytes: int = 2_000_000,
    max_side: int = 1024,
) -> ValidatedReferenceImage:
    try:
        raw = base64.b64decode(data_base64, validate=True)
    except Exception as exc:
        raise ValueError("reference image is not valid base64") from exc
    if not raw:
        raise ValueError("reference image is empty")
    if len(raw) > max_payload_bytes:
        raise ValueError("reference image exceeds upload limit")

    encoded = np.frombuffer(raw, dtype=np.uint8)
    image = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    if image is None or image.size == 0:
        raise ValueError("reference image could not be decoded")

    height, width = image.shape[:2]
    if width < 32 or height < 32:
        raise ValueError("reference image is too small")

    cap = max(128, int(max_side))
    scale = min(1.0, cap / float(max(width, height)))
    if scale < 1.0:
        width = max(32, int(round(width * scale)))
        height = max(32, int(round(height * scale)))
        image = cv2.resize(image, (width, height), interpolation=cv2.INTER_AREA)

    ok, normalized = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
    if not ok:
        raise ValueError("reference image normalization failed")
    jpeg = normalized.tobytes()
    return ValidatedReferenceImage(
        jpeg_bytes=jpeg,
        width=width,
        height=height,
        sha256=hashlib.sha256(jpeg).hexdigest(),
    )
