"""Real-time identity face swap (InsightFace inswapper / HyperSwap families).

A real swapper needs an identity vector, not a second photo: the source
face is encoded once with ArcFace, and the vector is applied to every live
frame. Supported engines (selected with OMNILIVE_SWAPPER_ENGINE):

- inswapper: 128px crop, ArcFace-112 template scaled up, source vector
  mapped through the swapper's own embedding matrix.
- hyper1a/1b/1c: 256px crops, FaceFusion arcface_128 template, plain
  L2-normalised ArcFace vector (no mapping), plus the model's own
  face-mask output.

Per-frame tracking re-detects inside a small region around the last box
every frame (cheap) and only scans the full frame to acquire or recover.
Keypoints pass through a One Euro filter (low lag, no ghosting). If the
face is briefly lost, the last swapped frame freezes instead of flashing
the real face; raw fall-through is counted in telemetry as a safety bug.

Without the weight files (or without a session reference photo) every entry
point degrades to untouched pass-through so transport stays testable.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock

import cv2
import numpy as np

from .config import settings
from .models import QualityProfile
from .regions import Region
from .session import registry
from .telemetry import (
    cache_reuse_counter,
    detector_window,
    dropped_inference_counter,
    inference_error_counter,
    model_window,
    overload_event_counter,
    pipeline_health,
    raw_leak_counter,
)
from .temporal import AdaptiveInferenceBudget, profile_defaults
from .vision import ScrfdFaceDetector, build_region_detector, resolve_vision_model_path

logger = logging.getLogger("omnilive.faceswap")

# Canonical 5-point templates (left eye, right eye, nose, mouth-left,
# mouth-right), normalized 0..1. The 128 template is NOT the 112 one
# scaled: it is a different fit, so each crop size uses its own.
_ARCFACE_112 = np.array(
    [
        [38.2946, 51.6963],
        [73.5318, 51.5014],
        [56.0252, 71.7366],
        [41.5493, 92.3655],
        [70.7299, 92.2041],
    ],
    dtype=np.float32,
) / 112.0
_ARCFACE_128 = np.array(
    [
        [0.36167656, 0.40387734],
        [0.63696719, 0.40235469],
        [0.50019687, 0.56044219],
        [0.38710391, 0.72160547],
        [0.61507734, 0.72034453],
    ],
    dtype=np.float32,
)

# Freeze instead of leaking the real face: only when the face was nearly
# still just before the loss (blink, brief cover). A fast-moving loss
# (head turn) clears immediately, so no stale swap ever floats around.
STILL_FREEZE_SECONDS = 0.5
STILL_SPEED_PX = 3.0
ROI_SCORE = 0.30

ENGINES = {
    "inswapper": {
        "file": "swapper/inswapper_128_fp16.onnx",
        "crop": 128,
        "template": "arcface_128",
        "mean": 0.0,
        "std": 255.0,
        "mapped": True,
        "mask_output": False,
    },
    "hyper1a": {
        "file": "swapper/hyperswap_1a_256.onnx",
        "crop": 256,
        "template": "arcface_128",
        "mean": 0.5,
        "std": 0.5,
        "mapped": False,
        "mask_output": True,
    },
    "hyper1b": {
        "file": "swapper/hyperswap_1b_256.onnx",
        "crop": 256,
        "template": "arcface_128",
        "mean": 0.5,
        "std": 0.5,
        "mapped": False,
        "mask_output": True,
    },
    "hyper1c": {
        "file": "swapper/hyperswap_1c_256.onnx",
        "crop": 256,
        "template": "arcface_128",
        "mean": 0.5,
        "std": 0.5,
        "mapped": False,
        "mask_output": True,
    },
}


def _template_points(name: str, size: int) -> np.ndarray:
    base = _ARCFACE_128 if name == "arcface_128" else _ARCFACE_112
    return (base * float(size)).astype(np.float32)


def _similarity(src5: np.ndarray, dst5: np.ndarray) -> np.ndarray | None:
    """Exact least-squares similarity transform (Umeyama, no RANSAC)."""
    src = np.asarray(src5, dtype=np.float64).reshape(5, 2)
    dst = np.asarray(dst5, dtype=np.float64).reshape(5, 2)
    mu_src = src.mean(axis=0)
    mu_dst = dst.mean(axis=0)
    centered_src = src - mu_src
    centered_dst = dst - mu_dst
    variance = float((centered_src**2).sum() / 5.0)
    if variance < 1e-9:
        return None
    cov = (centered_dst.T @ centered_src) / 5.0
    left, singular, right_t = np.linalg.svd(cov)
    reflect = np.eye(2)
    if np.linalg.det(left) * np.linalg.det(right_t) < 0:
        reflect[1, 1] = -1
    rotation = left @ reflect @ right_t
    scale = float(((singular * np.diag(reflect)).sum()) / variance)
    if not np.isfinite(scale) or scale <= 0:
        return None
    translation = mu_dst - scale * (rotation @ mu_src)
    matrix = np.hstack([scale * rotation, translation.reshape(2, 1)])
    return matrix.astype(np.float32)


def norm_crop(
    img: np.ndarray, kps: np.ndarray, image_size: int = 128, template: str = "arcface_112"
):
    """Align a face to a canonical template."""
    dst = _template_points(template, image_size)
    transform = _similarity(np.asarray(kps, dtype=np.float32).reshape(5, 2), dst)
    if transform is None:
        return None, None
    warped = cv2.warpAffine(
        img,
        transform,
        (image_size, image_size),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REPLICATE,
    )
    return warped, transform


class _OneEuro:
    """Low-lag keypoint smoothing (standard One Euro filter)."""

    def __init__(
        self, freq: float = 30.0, min_cutoff: float = 1.0, beta: float = 0.02,
        d_cutoff: float = 1.0,
    ):
        self.freq = freq
        self.min_cutoff = min_cutoff
        self.beta = beta
        self.d_cutoff = d_cutoff
        self.x_prev: float | None = None
        self.dx_prev = 0.0

    @staticmethod
    def _alpha(cutoff: float, freq: float) -> float:
        tau = 1.0 / (2.0 * math.pi * cutoff)
        return 1.0 / (1.0 + tau * freq)

    def __call__(self, x: float) -> float:
        value = float(x)
        if self.x_prev is None:
            self.x_prev = value
            return value
        dx = (value - self.x_prev) * self.freq
        alpha_d = self._alpha(self.d_cutoff, self.freq)
        dx_hat = alpha_d * dx + (1.0 - alpha_d) * self.dx_prev
        cutoff = self.min_cutoff + self.beta * abs(dx_hat)
        alpha = self._alpha(cutoff, self.freq)
        estimate = alpha * value + (1.0 - alpha) * self.x_prev
        self.x_prev = estimate
        self.dx_prev = dx_hat
        return estimate

    def reset(self, x: float | None = None) -> None:
        self.x_prev = None if x is None else float(x)
        self.dx_prev = 0.0


def _face_oval(size: int, template: str = "arcface_112") -> np.ndarray:
    """Feathered face-shaped mask in aligned-crop space (no square edges)."""
    dst = _template_points(template, size)
    left_eye, right_eye = dst[0], dst[1]
    eye_dist = float(np.linalg.norm(right_eye - left_eye)) + 1e-6
    cx = float((left_eye[0] + right_eye[0]) * 0.5)
    # Biased downward so the jaw, chin and beard zone sit inside the swap.
    cy = float(left_eye[1] + eye_dist * 0.55)
    rx = eye_dist * 1.10
    ry = eye_dist * 1.55
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    oval = (((xx - cx) / rx) ** 2 + ((yy - cy) / ry) ** 2) <= 1.0
    mask = np.zeros((size, size), dtype=np.float32)
    mask[oval] = 255.0
    kernel = max(7, int(round(size / 12.0)))
    if kernel % 2 == 0:
        kernel += 1
    mask = cv2.erode(mask, np.ones((kernel, kernel), np.uint8))
    return cv2.GaussianBlur(mask, (0, 0), sigmaX=float(size) / 32.0)


def paste_back(
    frame: np.ndarray,
    swapped: np.ndarray,
    transform: np.ndarray,
    extra_masks: list[np.ndarray] | None = None,
) -> np.ndarray:
    """Composite a swapped crop back into the full frame.

    Warps only the affected face area (not the whole frame), inside an
    eroded face oval multiplied by any extra masks (swapper / occlusion).
    The full-strength swapped face goes through; masking never eats it.
    """
    height, width = frame.shape[:2]
    size = int(swapped.shape[0])
    inverse = cv2.invertAffineTransform(transform)
    corners = np.array(
        [[0, 0], [size, 0], [size, size], [0, size]], dtype=np.float32
    )
    warped_corners = cv2.transform(corners.reshape(-1, 1, 2), inverse).reshape(-1, 2)
    x0 = max(0, int(np.floor(warped_corners[:, 0].min())))
    y0 = max(0, int(np.floor(warped_corners[:, 1].min())))
    x1 = min(width, int(np.ceil(warped_corners[:, 0].max())))
    y1 = min(height, int(np.ceil(warped_corners[:, 1].max())))
    if x1 <= x0 or y1 <= y0:
        return frame
    local = inverse.copy()
    local[0, 2] -= float(x0)
    local[1, 2] -= float(y0)
    region_w, region_h = x1 - x0, y1 - y0
    back = cv2.warpAffine(
        swapped,
        local,
        (region_w, region_h),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REPLICATE,
    )
    alpha = _face_oval(size)
    if extra_masks:
        for extra in extra_masks:
            if extra.shape[:2] != (size, size):
                extra = cv2.resize(extra, (size, size), interpolation=cv2.INTER_LINEAR)
            alpha = alpha * np.clip(extra.astype(np.float32), 0.0, 1.0)
    region_mask = cv2.warpAffine(
        alpha,
        local,
        (region_w, region_h),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=0.0,
    )
    region_mask = np.clip(region_mask, 0.0, 255.0) / 255.0
    target = frame[y0:y1, x0:x1].astype(np.float32)
    if back.shape[:2] != target.shape[:2]:
        back = cv2.resize(back, (region_w, region_h), interpolation=cv2.INTER_LINEAR)
    weight = region_mask[..., None] if region_mask.ndim == 2 else region_mask
    merged = target * (1.0 - weight) + back.astype(np.float32) * weight
    output = frame.copy()
    output[y0:y1, x0:x1] = np.clip(merged, 0, 255).astype(np.uint8)
    return output


def _match_skin(
    swapped: np.ndarray,
    ref_mean: np.ndarray,
    ref_std: np.ndarray,
    amount: float | np.ndarray = 1.0,
) -> np.ndarray:
    """Pull the swapped crop's skin tone toward the reference photo in LAB.

    Amount may be a per-pixel map: full reference tone in the face center
    fading to live tone at the edge, so no visible seam rings the face.
    """
    try:
        swapped_lab = cv2.cvtColor(swapped, cv2.COLOR_RGB2LAB).astype(np.float32)
        matched = np.empty_like(swapped_lab)
        for channel in range(3):
            src = swapped_lab[..., channel]
            matched[..., channel] = (src - float(src.mean())) * (
                (float(ref_std[channel]) + 5.0) / (float(src.std()) + 5.0)
            ) + float(ref_mean[channel])
        matched_rgb = cv2.cvtColor(
            np.clip(matched, 0, 255).astype(np.uint8), cv2.COLOR_LAB2RGB
        )
        weight = np.asarray(amount, dtype=np.float32)
        if weight.ndim == 2:
            weight = weight[..., None]
        blended = (
            swapped.astype(np.float32) * (1.0 - weight)
            + matched_rgb.astype(np.float32) * weight
        )
        return np.clip(blended, 0, 255).astype(np.uint8)
    except cv2.error:
        return swapped


class _LazySwapSession:
    """Minimal lazy ONNX holder with GPU-first provider preference."""

    def __init__(self, model_id: str):
        self.model_id = model_id
        self._session = None
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
                # NOTE: TensorRT is deliberately NOT listed. When its system
                # libraries are absent, merely including it makes ORT abort
                # GPU setup and fall back to CPU, skipping CUDA as well.
                preferred = [
                    provider
                    for provider in (
                        "CUDAExecutionProvider",
                        "CPUExecutionProvider",
                    )
                    if provider in available
                ]
                if not preferred:
                    preferred = ["CPUExecutionProvider"]
                self._session = ort.InferenceSession(str(path), providers=preferred)
                self._providers = list(self._session.get_providers())
                self._last_error = None
            except Exception as exc:
                self._last_error = str(exc)
                self._session = None
            return self._session

    def status(self) -> dict:
        return {
            "model_id": self.model_id,
            "configured_file_exists": self.configured_file_exists,
            "loaded": self._session is not None,
            "providers": self.providers,
            "last_error": self.last_error,
        }


@dataclass
class SwapStream:
    profile_name: str
    budget: AdaptiveInferenceBudget | None = None
    box: Region | None = None
    kps: np.ndarray | None = None
    euro: list[_OneEuro] = field(default_factory=list)
    frame_index: int = 0
    prev_centroid: np.ndarray | None = None
    last_speed: float = 0.0
    cached_crop: np.ndarray | None = None
    cached_masks: list[np.ndarray] = field(default_factory=list)
    cached_at: float = 0.0
    frozen: np.ndarray | None = None
    frozen_at: float = 0.0
    last_seen: float = field(default_factory=time.perf_counter)

    def configure(self, profile_name: str) -> None:
        if self.budget is None or self.profile_name != profile_name:
            self.profile_name = profile_name
            self.budget = AdaptiveInferenceBudget(profile_defaults(profile_name))

    def reset_tracking(self) -> None:
        self.box = None
        self.kps = None
        self.euro = []
        self.prev_centroid = None
        self.last_speed = 0.0


class FaceSwapEngine:
    """Session-reference-driven real-time face swapper."""

    requires_reference = True

    def __init__(self, detector=None, swapper_engine: str | None = None):
        self.detector = detector or build_region_detector(max_regions=1)
        # Low-threshold full-frame scanner: catches profile and partial
        # faces the strict detector would miss. Seeding a brand-new track
        # still needs a confident hit (see _track).
        self.scan_detector = ScrfdFaceDetector(
            settings.face_detector_model,
            confidence=ROI_SCORE,
            nms_threshold=settings.face_detector_nms,
            max_regions=1,
        )
        self.swapper_name = swapper_engine or settings.swapper_engine
        if self.swapper_name not in ENGINES:
            logger.warning("unknown swapper %s, using inswapper", self.swapper_name)
            self.swapper_name = "inswapper"
        self.spec = ENGINES[self.swapper_name]
        self.swapper = _LazySwapSession(self.spec["file"])
        self.arcface = _LazySwapSession(settings.arcface_model)
        self.enhancer = _LazySwapSession(settings.enhancer_model)
        self.occluder = _LazySwapSession(settings.xseg_model)
        self._emap: np.ndarray | None = None
        self._sources: dict[str, dict] = {}
        self._stream_sha: dict[str, str] = {}
        self._streams: dict[str, SwapStream] = {}
        self._lock = Lock()
        self._infer_lock = Lock()
        self._source_ready = False

    @property
    def available(self) -> bool:
        return self.swapper.configured_file_exists and self.arcface.configured_file_exists

    @property
    def enhancer_enabled(self) -> bool:
        return bool(settings.enhancer_enabled) and self.enhancer.configured_file_exists

    @property
    def occluder_enabled(self) -> bool:
        return bool(settings.xseg_enabled) and self.occluder.configured_file_exists

    def _emap_matrix(self) -> np.ndarray | None:
        if self._emap is not None:
            return self._emap
        cache = Path(settings.model_root).resolve() / "swapper" / "inswapper_emap.npy"
        try:
            if cache.is_file():
                self._emap = np.load(str(cache)).astype(np.float32)
                return self._emap
        except Exception as exc:
            logger.warning("swapper emap cache unreadable: %s", exc)
        try:
            import onnx
            from onnx import numpy_helper

            swapper_path = resolve_vision_model_path(settings.swapper_model)
            model = onnx.load(str(swapper_path))
            emap = numpy_helper.to_array(model.graph.initializer[-1]).astype(np.float32)
            if emap.shape != (512, 512):
                logger.warning("unexpected swapper emap shape %s", emap.shape)
                return None
            self._emap = emap
            try:
                np.save(str(cache), emap)
            except Exception as exc:
                logger.warning("swapper emap cache write failed: %s", exc)
            return self._emap
        except ImportError:
            logger.warning("onnx package is required to read the swapper embedding map")
            return None
        except Exception as exc:
            logger.warning("swapper emap extraction failed: %s", exc)
            return None

    def _reference_rgb(self, stream_key: str) -> tuple[np.ndarray | None, str | None]:
        reference = registry.get_reference_image(stream_key)
        if reference is None:
            return None, None
        encoded = np.frombuffer(reference.jpeg_bytes, dtype=np.uint8)
        bgr = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
        if bgr is None or bgr.size == 0:
            return None, None
        return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB), reference.sha256

    def source_latent(self, stream_key: str) -> np.ndarray | None:
        """Identity vector for the session reference photo (computed once)."""
        rgb, sha = self._reference_rgb(stream_key)
        if rgb is None or sha is None:
            self._source_ready = False
            return None
        with self._lock:
            cached = self._sources.get(sha)
            if cached is not None:
                self._stream_sha[stream_key] = sha
        if cached is not None:
            self._source_ready = True
            return cached["latent_mapped"] if self.spec["mapped"] else cached["latent"]
        detections = [r for r in self.detector.detect(rgb) if r.kps is not None]
        if not detections:
            logger.warning("no alignable face in reference image for stream %s", stream_key)
            self._source_ready = False
            return None
        best = max(detections, key=lambda r: r.area)
        kps = np.asarray(best.kps, dtype=np.float32).reshape(5, 2)
        aligned, _ = norm_crop(rgb, kps, 112, "arcface_112")
        if aligned is None:
            self._source_ready = False
            return None
        session = self.arcface.load()
        if session is None:
            self._source_ready = False
            return None
        meta = session.get_inputs()[0]
        # RGB stays RGB: the models train on RGB, so no channel swap.
        blob = cv2.dnn.blobFromImage(
            aligned, 1.0 / 127.5, (112, 112), (127.5, 127.5, 127.5), swapRB=False
        )
        with self._infer_lock:
            embedding = np.asarray(session.run(None, {meta.name: blob})[0]).reshape(-1)
        norm = float(np.linalg.norm(embedding)) + 1e-9
        embedding = (embedding / norm).astype(np.float32)
        mapped = None
        if self.spec["mapped"]:
            emap = self._emap_matrix()
            if emap is None:
                self._source_ready = False
                return None
            vector = embedding.reshape(1, -1) @ emap
            mapped = (vector / (float(np.linalg.norm(vector)) + 1e-9)).astype(np.float32)
        interior = _face_oval(112, "arcface_112") > 127.0
        tone_lab = cv2.cvtColor(aligned, cv2.COLOR_RGB2LAB).astype(np.float32)
        face_pixels = tone_lab[interior]
        tone_mean = face_pixels.mean(axis=0).astype(np.float32)
        tone_std = face_pixels.std(axis=0).astype(np.float32)
        with self._lock:
            self._sources[sha] = {
                "latent": embedding.reshape(1, -1),
                "latent_mapped": mapped,
                "tone_mean": tone_mean,
                "tone_std": tone_std,
            }
            self._stream_sha[stream_key] = sha
            if len(self._sources) > 16:
                oldest = next(iter(self._sources))
                self._sources.pop(oldest, None)
        self._source_ready = True
        logger.info("face-swap identity ready for stream %s", stream_key)
        return mapped if self.spec["mapped"] else embedding.reshape(1, -1)

    def source_tone(self, stream_key: str) -> tuple[np.ndarray, np.ndarray] | None:
        """LAB skin-tone statistics of the session reference face itself."""
        with self._lock:
            sha = self._stream_sha.get(stream_key)
            entry = self._sources.get(sha) if sha is not None else None
        if entry is None:
            if self.source_latent(stream_key) is None:
                return None
            with self._lock:
                sha = self._stream_sha.get(stream_key)
                entry = self._sources.get(sha) if sha is not None else None
        if entry is None:
            return None
        return entry["tone_mean"], entry["tone_std"]

    def _enhance(self, crop: np.ndarray, size: int) -> np.ndarray:
        """Optional detail pass (GPEN). Never allowed to break a frame."""
        if not self.enhancer_enabled:
            return crop
        try:
            session = self.enhancer.load()
            if session is None:
                return crop
            up = cv2.resize(crop, (256, 256), interpolation=cv2.INTER_CUBIC)
            blob = (up.astype(np.float32) / 127.5 - 1.0).transpose(2, 0, 1)[None, ...]
            meta = session.get_inputs()[0]
            with self._infer_lock:
                out = np.asarray(session.run(None, {meta.name: blob})[0])
            enhanced = np.clip((out[0].transpose(1, 2, 0) + 1.0) * 127.5, 0, 255)
            if not np.isfinite(float(enhanced.mean())):
                return crop
            if float(enhanced.mean()) < 5.0 or float(enhanced.mean()) > 250.0:
                return crop
            enhanced = cv2.resize(
                enhanced.astype(np.uint8), (size, size), interpolation=cv2.INTER_AREA
            )
            strength = max(0.0, min(1.0, float(settings.enhancer_strength)))
            blended = crop.astype(np.float32) * (1.0 - strength) + enhanced.astype(
                np.float32
            ) * strength
            return np.clip(blended, 0, 255).astype(np.uint8)
        except Exception as exc:
            logger.warning("face enhancer skipped: %s", exc)
            return crop

    def _occlusion(self, aligned: np.ndarray, size: int) -> np.ndarray | None:
        """Paste-allowed mask from the xseg occluder (FaceFusion convention).

        Input is BGR 0-1 channels-last (no channel flip). Output is 1 where
        the swapped face may show and 0 over occluders/background, sharpened
        exactly like FaceFusion sharpens it.
        """
        if not self.occluder_enabled:
            return None
        try:
            session = self.occluder.load()
            if session is None:
                return None
            frame = aligned
            if frame.shape[0] != 256:
                frame = cv2.resize(frame, (256, 256), interpolation=cv2.INTER_LINEAR)
            bgr = frame[:, :, ::-1]
            blob = np.expand_dims(bgr.astype(np.float32) / 255.0, axis=0)
            meta = session.get_inputs()[0]
            with self._infer_lock:
                out = np.asarray(session.run(None, {meta.name: blob})[0])
            mask = np.clip(out.reshape(256, 256), 0.0, 1.0).astype(np.float32)
            if mask.mean() < 0.005 or mask.mean() > 0.995:
                return None
            mask = cv2.GaussianBlur(mask, (0, 0), 5)
            mask = (np.clip(mask, 0.5, 1.0) - 0.5) * 2.0
            if size != 256:
                mask = cv2.resize(mask, (size, size), interpolation=cv2.INTER_LINEAR)
            return np.clip(mask, 0.0, 1.0).astype(np.float32)
        except Exception as exc:
            logger.warning("occluder skipped: %s", exc)
            return None

    def _context(self, stream_key: str, profile_name: str) -> SwapStream:
        now = time.perf_counter()
        with self._lock:
            context = self._streams.get(stream_key)
            if context is None:
                context = SwapStream(profile_name=profile_name)
                context.configure(profile_name)
                self._streams[stream_key] = context
            else:
                context.configure(profile_name)
            context.last_seen = now
        pipeline_health.update(active_swap_streams=len(self._streams))
        return context

    def release_stream(self, stream_key: str) -> None:
        with self._lock:
            self._streams.pop(stream_key, None)
            active = len(self._streams)
        pipeline_health.update(active_swap_streams=active)

    def _filter_kps(self, context: SwapStream, fresh: np.ndarray) -> np.ndarray:
        flat = np.asarray(fresh, dtype=np.float32).reshape(-1)
        if len(context.euro) != flat.shape[0]:
            context.euro = [_OneEuro() for _ in range(flat.shape[0])]
        smoothed = np.array([f(value) for f, value in zip(context.euro, flat)])
        return smoothed.reshape(5, 2).astype(np.float32)

    def _track(self, rgb: np.ndarray, context: SwapStream) -> bool:
        """Full-frame re-detect every frame with Euro-smoothed keypoints.

        A fresh full scan costs a few milliseconds on the L4 and recovers
        instantly from turns, while the low detector threshold plus the
        existing-track gate keeps profile faces without inviting
        false positives in empty rooms.
        """
        started = time.perf_counter()
        try:
            found = [r for r in self.scan_detector.detect(rgb) if r.kps is not None]
            if not found:
                detector_window.add((time.perf_counter() - started) * 1000.0)
                return False
            best = max(found, key=lambda r: (r.score, r.area))
            if context.box is None and best.score < settings.face_detector_confidence:
                detector_window.add((time.perf_counter() - started) * 1000.0)
                return False
            context.box = best
            fresh = np.asarray(best.kps, dtype=np.float32).reshape(5, 2)
            centroid = fresh.mean(axis=0)
            if context.prev_centroid is not None:
                context.last_speed = float(
                    np.linalg.norm(centroid - context.prev_centroid)
                )
            context.prev_centroid = centroid.astype(np.float32)
            if context.kps is None:
                context.euro = []
            context.kps = self._filter_kps(context, fresh)
            detector_window.add((time.perf_counter() - started) * 1000.0)
            return True
        except Exception as exc:
            logger.warning("swap tracking failed: %s", exc)
            return False

    def _swap_crop(
        self, aligned: np.ndarray, latent: np.ndarray, size: int
    ) -> tuple[np.ndarray | None, list[np.ndarray]]:
        session = self.swapper.load()
        if session is None:
            return None, []
        # RGB stays RGB: the swap models train on RGB, so no channel swap.
        blob = cv2.dnn.blobFromImage(
            aligned,
            1.0 / self.spec["std"],
            (size, size),
            (self.spec["mean"],) * 3,
            swapRB=False,
        )
        inputs = session.get_inputs()
        names = {meta.name for meta in inputs}
        live_name = next(
            (meta.name for meta in inputs if "target" in meta.name.lower()), inputs[0].name
        )
        ref_name = next(
            (meta.name for meta in inputs if meta.name != live_name), None
        )
        feeds = {live_name: blob}
        if ref_name is not None:
            feeds[ref_name] = latent
        elif len(inputs) > 1:
            return None, []
        with self._infer_lock:
            results = session.run(None, feeds)
        outputs = session.get_outputs()
        decoded = None
        masks: list[np.ndarray] = []
        for meta, value in zip(outputs, results):
            array = np.asarray(value)
            shape = list(meta.shape)
            channels = shape[1] if len(shape) == 4 else 0
            if array.ndim == 4 and channels == 1:
                masks.append(np.clip(array[0, 0], 0.0, 1.0).astype(np.float32))
            elif decoded is None:
                image = array[0].transpose(1, 2, 0) * float(self.spec["std"]) + float(
                    self.spec["mean"]
                ) * 255.0
                # Model output is RGB, matching our frames: no channel flip.
                decoded = np.clip(image, 0.0, 255.0).astype(np.uint8)
        if decoded is None:
            return None, []
        return decoded, masks

    def process_frame(
        self, rgb: np.ndarray, stream_key: str, profile: QualityProfile
    ) -> np.ndarray:
        height, width = rgb.shape[:2]
        context = self._context(stream_key, profile.value)
        context.frame_index += 1
        now = time.perf_counter()
        budget = context.budget
        assert budget is not None

        if not self.available:
            pipeline_health.update(face_swap_ready=False)
            return rgb
        latent = self.source_latent(stream_key)
        if latent is None:
            pipeline_health.update(face_swap_ready=False, source_ready=False)
            return rgb
        size = int(self.spec["crop"])
        template = str(self.spec["template"])

        tracked = self._track(rgb, context)
        if not tracked or context.kps is None:
            return self._no_face(rgb, context, width, height, now)

        if not budget.should_infer(now):
            dropped_inference_counter.increment()
            if context.cached_crop is None or not self._cache_valid(context, now):
                return self._reuse_or_hold(rgb, context, width, height, now)
            return self._paste_cached(rgb, context, width, height)

        aligned, transform = norm_crop(rgb, context.kps, size, template)
        if aligned is None or transform is None:
            return self._no_face(rgb, context, width, height, now)
        overload_before = budget.overload_events
        started = time.perf_counter()
        try:
            swapped, model_masks = self._swap_crop(aligned, latent, size)
            if swapped is None:
                raise RuntimeError("swapper produced no face")
            swapped = self._enhance(swapped, size)
            tone = self.source_tone(stream_key)
            if tone is not None:
                # Full reference tone in the face center, fading to live
                # tone at the edge: identity tone with no visible seam.
                oval_map = _face_oval(size, template) / 255.0
                swapped = _match_skin(swapped, tone[0], tone[1], oval_map)
            neural_ms = (time.perf_counter() - started) * 1000.0
            model_window.add(neural_ms)
            budget.observe_inference(neural_ms)
            if budget.overload_events > overload_before:
                overload_event_counter.increment(budget.overload_events - overload_before)
        except Exception as exc:
            inference_error_counter.increment()
            logger.warning("face-swap inference failed: %s", exc)
            return self._reuse_or_hold(rgb, context, width, height, now)

        masks = list(model_masks)
        occlusion = self._occlusion(aligned, size)
        if occlusion is not None:
            masks.append(occlusion)
        try:
            current = norm_crop(rgb, context.kps, size, template)[1]
            paste_matrix = current if current is not None else transform
            output = paste_back(rgb, swapped, paste_matrix, masks or None)
        except cv2.error as exc:
            inference_error_counter.increment()
            logger.warning("face-swap paste-back failed: %s", exc)
            return self._reuse_or_hold(rgb, context, width, height, now)
        context.cached_crop = swapped
        context.cached_masks = masks
        context.cached_at = now
        context.frozen = output
        context.frozen_at = now
        pipeline_health.update(
            face_swap_ready=True,
            source_ready=True,
            target_neural_fps=round(budget.target_neural_fps, 2),
        )
        return output

    def _paste_cached(
        self, rgb: np.ndarray, context: SwapStream, width: int, height: int
    ) -> np.ndarray:
        assert context.cached_crop is not None and context.kps is not None
        size = int(self.spec["crop"])
        template = str(self.spec["template"])
        current = _similarity(
            np.asarray(context.kps, dtype=np.float32).reshape(5, 2),
            _template_points(template, size),
        )
        if current is None:
            return rgb
        try:
            return paste_back(rgb, context.cached_crop, current, context.cached_masks or None)
        except cv2.error:
            return rgb

    def _reuse_or_hold(
        self, rgb: np.ndarray, context: SwapStream, width: int, height: int, now: float
    ) -> np.ndarray:
        if (
            context.cached_crop is not None
            and context.kps is not None
            and self._cache_valid(context, now)
        ):
            cache_reuse_counter.increment()
            return self._paste_cached(rgb, context, width, height)
        return self._no_face(rgb, context, width, height, now)

    def _no_face(
        self, rgb: np.ndarray, context: SwapStream, width: int, height: int, now: float
    ) -> np.ndarray:
        pipeline_health.update(face_swap_ready=True, source_ready=True)
        # Freeze only a face that was nearly still a moment ago (a blink or
        # brief cover). Anything moving fast (a head turn) clears at once,
        # so a stale swap can never float around detached from the face.
        if (
            context.frozen is not None
            and (now - context.frozen_at) <= STILL_FREEZE_SECONDS
            and context.last_speed <= STILL_SPEED_PX
        ):
            cache_reuse_counter.increment()
            return context.frozen
        raw_leak_counter.increment()
        context.reset_tracking()
        return rgb

    def _cache_valid(self, context: SwapStream, now: float) -> bool:
        if context.cached_crop is None:
            return False
        ttl_ms = profile_defaults(context.profile_name).cache_ttl_ms
        return (now - context.cached_at) * 1000.0 <= ttl_ms

    def status(self) -> dict:
        return {
            "face_swap_available": self.available,
            "face_swap_source_ready": self._source_ready,
            "swapper_engine": self.swapper_name,
            "swapper_model": self.swapper.status(),
            "arcface_model": self.arcface.status(),
            "enhancer_model": self.enhancer.status(),
            "enhancer_enabled": self.enhancer_enabled,
            "occluder_model": self.occluder.status(),
            "occluder_enabled": self.occluder_enabled,
            "detector_backend": self.detector.name,
            "emap_loaded": self._emap is not None,
            "cached_identities": len(self._sources),
            "active_swap_streams": len(self._streams),
        }

    def preload(self) -> dict:
        self.swapper.load()
        self.arcface.load()
        if self.enhancer_enabled:
            self.enhancer.load()
        if self.occluder_enabled:
            self.occluder.load()
        if self.spec["mapped"]:
            self._emap_matrix()
        pipeline_health.update(
            face_swap_ready=self.available,
            swapper_backend=self.swapper.providers[0] if self.swapper.providers else "none",
        )
        return self.status()


engine = FaceSwapEngine()
