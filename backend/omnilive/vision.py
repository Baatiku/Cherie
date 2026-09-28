from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Protocol

import cv2
import numpy as np

from .config import settings
from .regions import OpenCvHaarFaceDetector, Region, RegionDetector


def resolve_vision_model_path(
    model_id: str,
    *,
    root: str | Path | None = None,
    require_exists: bool = True,
) -> Path:
    base = Path(root or settings.model_root).resolve()
    candidate = (base / model_id).resolve()
    if candidate != base and base not in candidate.parents:
        raise ValueError("vision model path escapes configured model root")
    if candidate.suffix.lower() != ".onnx":
        candidate = candidate.with_suffix(".onnx")
    if require_exists and not candidate.is_file():
        raise FileNotFoundError("vision model not found: " + str(candidate))
    return candidate


class _LazyVisionSession:
    def __init__(self, model_id: str):
        self.model_id = model_id
        self._session = None
        self._path: Path | None = None
        self._providers: list[str] = []
        self._last_error: str | None = None
        self._lock = Lock()

    @property
    def providers(self) -> list[str]:
        return list(self._providers)

    @property
    def last_error(self) -> str | None:
        return self._last_error

    @property
    def model_path(self) -> str | None:
        return str(self._path) if self._path else None

    @property
    def configured_file_exists(self) -> bool:
        try:
            return resolve_vision_model_path(self.model_id).is_file()
        except Exception:
            return False

    def load(self):
        if self._session is not None:
            return self._session
        with self._lock:
            if self._session is not None:
                return self._session
            try:
                path = resolve_vision_model_path(self.model_id)
                import onnxruntime as ort

                available = ort.get_available_providers()
                preferred = [
                    provider
                    for provider in (
                        "TensorrtExecutionProvider",
                        "CUDAExecutionProvider",
                        "CPUExecutionProvider",
                    )
                    if provider in available
                ]
                if not preferred:
                    preferred = ["CPUExecutionProvider"]
                self._session = ort.InferenceSession(str(path), providers=preferred)
                self._path = path
                self._providers = list(self._session.get_providers())
                self._last_error = None
            except Exception as exc:
                self._last_error = str(exc)
                self._session = None
            return self._session

    def status(self) -> dict:
        return {
            "model_id": self.model_id,
            "model_path": self.model_path,
            "configured_file_exists": self.configured_file_exists,
            "loaded": self._session is not None,
            "providers": self.providers,
            "last_error": self.last_error,
        }


def _nms(regions: list[Region], threshold: float) -> list[Region]:
    ordered = sorted(regions, key=lambda item: item.score, reverse=True)
    kept: list[Region] = []
    while ordered:
        current = ordered.pop(0)
        kept.append(current)
        ordered = [candidate for candidate in ordered if current.iou(candidate) <= threshold]
    return kept


class ScrfdFaceDetector:
    """Lazy SCRFD ONNX detector with TensorRT/CUDA/CPU provider preference."""

    name = "scrfd_onnx"

    def __init__(
        self,
        model_id: str,
        confidence: float = 0.5,
        nms_threshold: float = 0.4,
        max_regions: int = 1,
    ):
        self.runtime = _LazyVisionSession(model_id)
        self.confidence = max(0.01, min(0.99, float(confidence)))
        self.nms_threshold = max(0.05, min(0.95, float(nms_threshold)))
        self.max_regions = max(1, int(max_regions))
        self.operational = False

    @property
    def available(self) -> bool:
        return self.runtime.configured_file_exists

    @property
    def providers(self) -> list[str]:
        return self.runtime.providers

    @property
    def last_error(self) -> str | None:
        return self.runtime.last_error

    def detect(self, rgb: np.ndarray) -> list[Region]:
        if rgb.size == 0:
            return []
        session = self.runtime.load()
        self.operational = session is not None
        if session is None:
            return []

        input_meta = session.get_inputs()[0]
        shape = input_meta.shape
        target_h = int(shape[2]) if len(shape) == 4 and isinstance(shape[2], int) else 640
        target_w = int(shape[3]) if len(shape) == 4 and isinstance(shape[3], int) else 640
        target_h = max(64, target_h)
        target_w = max(64, target_w)

        resized = cv2.resize(rgb, (target_w, target_h), interpolation=cv2.INTER_LINEAR)
        blob = (resized.astype(np.float32) - 127.5) / 128.0
        blob = np.transpose(blob, (2, 0, 1))[None, ...]
        outputs = [np.asarray(value) for value in session.run(None, {input_meta.name: blob})]

        pairs = self._output_pairs(outputs)
        if not pairs:
            self.operational = False
            return []

        height, width = rgb.shape[:2]
        scale_x = width / float(target_w)
        scale_y = height / float(target_h)
        regions: list[Region] = []
        strides = (8, 16, 32, 64, 128)

        for level, (score_output, bbox_output) in enumerate(pairs):
            if level >= len(strides):
                break
            stride = strides[level]
            scores = self._scores(score_output)
            boxes = np.asarray(bbox_output).squeeze()
            if boxes.size < 4:
                continue
            boxes = boxes.reshape(-1, 4).astype(np.float32)
            count = min(len(scores), len(boxes))
            if count <= 0:
                continue
            scores = scores[:count]
            boxes = boxes[:count]

            feature_h = max(1, target_h // stride)
            feature_w = max(1, target_w // stride)
            grid_count = feature_h * feature_w
            anchors = max(1, int(np.ceil(count / float(grid_count))))
            yy, xx = np.mgrid[0:feature_h, 0:feature_w]
            centers = np.stack((xx, yy), axis=-1).reshape(-1, 2).astype(np.float32) * float(stride)
            if anchors > 1:
                centers = np.repeat(centers, anchors, axis=0)
            centers = centers[:count]

            indexes = np.flatnonzero(scores >= self.confidence)
            for index in indexes:
                cx, cy = centers[index]
                left, top, right, bottom = boxes[index] * float(stride)
                x0 = (cx - left) * scale_x
                y0 = (cy - top) * scale_y
                x1 = (cx + right) * scale_x
                y1 = (cy + bottom) * scale_y
                region = Region(
                    int(round(x0)),
                    int(round(y0)),
                    int(round(x1 - x0)),
                    int(round(y1 - y0)),
                    float(scores[index]),
                ).clamp(width, height)
                if region is not None:
                    regions.append(region)

        return _nms(regions, self.nms_threshold)[: self.max_regions]

    @staticmethod
    def _scores(value: np.ndarray) -> np.ndarray:
        array = np.asarray(value).squeeze()
        if array.ndim == 0:
            return array.reshape(1).astype(np.float32)
        if array.ndim >= 2 and array.shape[-1] > 1:
            array = array.reshape(-1, array.shape[-1])[:, -1]
        return array.reshape(-1).astype(np.float32)

    @staticmethod
    def _output_pairs(outputs: list[np.ndarray]) -> list[tuple[np.ndarray, np.ndarray]]:
        if len(outputs) >= 6:
            levels = min(5, len(outputs) // 2)
            score_outputs = outputs[:levels]
            bbox_outputs = outputs[levels : levels * 2]
            if all(np.asarray(value).size >= 4 for value in bbox_outputs):
                return list(zip(score_outputs, bbox_outputs))

        scores: list[np.ndarray] = []
        boxes: list[np.ndarray] = []
        for output in outputs:
            array = np.asarray(output).squeeze()
            if array.ndim >= 1 and array.shape[-1:] == (4,):
                boxes.append(output)
            else:
                scores.append(output)
        return list(zip(scores, boxes))


class FallbackRegionDetector:
    """Use the production detector when operational and fall back only on runtime failure."""

    def __init__(self, primary: RegionDetector, fallback: RegionDetector):
        self.primary = primary
        self.fallback = fallback
        self.name = f"{primary.name}+{fallback.name}_fallback"

    def detect(self, rgb: np.ndarray) -> list[Region]:
        result = self.primary.detect(rgb)
        if getattr(self.primary, "operational", True):
            return result
        return self.fallback.detect(rgb)


@dataclass(frozen=True)
class SemanticLandmarks:
    points: np.ndarray
    score: float
    schema: str

    @property
    def count(self) -> int:
        return int(len(self.points))


class SemanticLandmarkProvider(Protocol):
    name: str

    def detect(self, rgb: np.ndarray, region: Region) -> SemanticLandmarks | None: ...


class OnnxFaceLandmarkProvider:
    """Generic face landmark ONNX adapter for 5/68/106-point image-to-points models."""

    name = "onnx_face_landmarks"

    def __init__(self, model_id: str):
        self.runtime = _LazyVisionSession(model_id)

    @property
    def available(self) -> bool:
        return self.runtime.configured_file_exists

    @property
    def providers(self) -> list[str]:
        return self.runtime.providers

    @property
    def last_error(self) -> str | None:
        return self.runtime.last_error

    def detect(self, rgb: np.ndarray, region: Region) -> SemanticLandmarks | None:
        session = self.runtime.load()
        if session is None:
            return None
        height, width = rgb.shape[:2]
        bounded = region.clamp(width, height)
        if bounded is None:
            return None

        crop = rgb[
            bounded.y : bounded.y + bounded.height,
            bounded.x : bounded.x + bounded.width,
        ]
        input_meta = session.get_inputs()[0]
        shape = input_meta.shape
        nchw = len(shape) == 4 and (shape[1] == 3 or shape[1] == "3")
        if nchw:
            target_h = int(shape[2]) if isinstance(shape[2], int) else 192
            target_w = int(shape[3]) if isinstance(shape[3], int) else 192
        else:
            target_h = int(shape[1]) if len(shape) == 4 and isinstance(shape[1], int) else 192
            target_w = int(shape[2]) if len(shape) == 4 and isinstance(shape[2], int) else 192

        resized = cv2.resize(crop, (target_w, target_h), interpolation=cv2.INTER_LINEAR)
        value = resized.astype(np.float32) / 255.0
        tensor = np.transpose(value, (2, 0, 1))[None, ...] if nchw else value[None, ...]
        output = np.asarray(session.run(None, {input_meta.name: tensor})[0]).squeeze()
        points = self._points(output)
        if points is None:
            return None

        normalized = points.astype(np.float32)
        xy = normalized[:, :2]
        if float(np.nanmin(xy)) >= -1.5 and float(np.nanmax(xy)) <= 1.5:
            if float(np.nanmin(xy)) < -0.05:
                xy = (xy + 1.0) * 0.5
        else:
            xy[:, 0] /= float(max(1, target_w))
            xy[:, 1] /= float(max(1, target_h))

        xy[:, 0] = bounded.x + np.clip(xy[:, 0], 0.0, 1.0) * bounded.width
        xy[:, 1] = bounded.y + np.clip(xy[:, 1], 0.0, 1.0) * bounded.height
        schema = {5: "face_5", 68: "face_68", 106: "face_106"}.get(len(xy), f"face_{len(xy)}")
        return SemanticLandmarks(points=xy, score=bounded.score, schema=schema)

    @staticmethod
    def _points(output: np.ndarray) -> np.ndarray | None:
        array = np.asarray(output)
        if array.ndim >= 2 and array.shape[-1] in (2, 3):
            return array.reshape(-1, array.shape[-1])
        flat = array.reshape(-1)
        for dimensions in (2, 3):
            if len(flat) >= dimensions * 5 and len(flat) % dimensions == 0:
                return flat.reshape(-1, dimensions)
        return None


@dataclass(frozen=True)
class AlignedFace:
    image: np.ndarray
    transform: np.ndarray
    source_schema: str


def _five_points(landmarks: SemanticLandmarks) -> np.ndarray | None:
    points = landmarks.points[:, :2].astype(np.float32)
    if landmarks.schema == "face_5" and len(points) >= 5:
        return points[:5]
    if landmarks.schema == "face_68" and len(points) >= 68:
        left_eye = points[36:42].mean(axis=0)
        right_eye = points[42:48].mean(axis=0)
        return np.stack((left_eye, right_eye, points[30], points[48], points[54]))
    return None


def align_face(rgb: np.ndarray, landmarks: SemanticLandmarks, size: int = 256) -> AlignedFace | None:
    source = _five_points(landmarks)
    if source is None:
        return None
    side = max(64, int(size))
    canonical = np.array(
        [
            [0.31556875, 0.46157411],
            [0.68262292, 0.46157411],
            [0.50026250, 0.64050536],
            [0.34947188, 0.82469195],
            [0.65343646, 0.82469195],
        ],
        dtype=np.float32,
    )
    target = canonical * float(side)
    transform, _ = cv2.estimateAffinePartial2D(source, target, method=cv2.LMEDS)
    if transform is None:
        return None
    aligned = cv2.warpAffine(
        rgb,
        transform,
        (side, side),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REFLECT_101,
    )
    return AlignedFace(image=aligned, transform=transform.astype(np.float32), source_schema=landmarks.schema)


def build_region_detector(max_regions: int = 1) -> RegionDetector:
    fallback = OpenCvHaarFaceDetector(max_regions=max_regions)
    backend = settings.detector_backend
    if backend in {"haar", "opencv", "opencv_haar"}:
        return fallback
    if backend not in {"auto", "scrfd"}:
        return fallback

    primary = ScrfdFaceDetector(
        settings.face_detector_model,
        confidence=settings.face_detector_confidence,
        nms_threshold=settings.face_detector_nms,
        max_regions=max_regions,
    )
    if backend == "auto" and not primary.available:
        return fallback
    return FallbackRegionDetector(primary, fallback)


def build_landmark_provider() -> SemanticLandmarkProvider | None:
    backend = settings.landmark_backend
    if backend in {"none", "off", "disabled"}:
        return None
    if backend not in {"auto", "onnx"}:
        return None
    provider = OnnxFaceLandmarkProvider(settings.face_landmark_model)
    if backend == "auto" and not provider.available:
        return None
    return provider


def vision_status(detector: RegionDetector, landmark_provider: SemanticLandmarkProvider | None) -> dict:
    detector_primary = getattr(detector, "primary", detector)
    landmark_runtime = getattr(landmark_provider, "runtime", None)
    detector_runtime = getattr(detector_primary, "runtime", None)
    return {
        "detector_backend": detector.name,
        "detector_model": detector_runtime.status() if detector_runtime is not None else None,
        "landmark_backend": getattr(landmark_provider, "name", "none"),
        "landmark_model": landmark_runtime.status() if landmark_runtime is not None else None,
        "face_alignment_enabled": settings.face_alignment_enabled,
        "face_alignment_size": settings.face_alignment_size,
        "person_detector_model": settings.person_detector_model,
    }
