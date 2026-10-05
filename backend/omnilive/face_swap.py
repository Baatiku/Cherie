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

# Freeze instead of leaking the real face: brief hold covers blinks, nods
# and zoom steps. Extrapolation carries fast motion for a few frames;
# a longer loss still clears so no stale swap ever floats around.
STILL_FREEZE_SECONDS = 0.8
STILL_SPEED_PX = 25.0  # raised: nod-down / zoom shifts easily exceed 12 px/frame
HOLD_FRAMES = 5
ROI_SCORE = 0.30
# Acquisition accepts the low-threshold scan when keypoints exist (profile,
# pitch-down, small/zoomed-out faces score low but align fine). Box-only
# hits still need the strict confidence to avoid false tracks.
ACQUIRE_KPS_SCORE = 0.30
MIN_FACE_PX = 28

ENGINES = {
    "inswapper": {
        "file": "swapper/inswapper_128_fp16.onnx",
        "crop": 128,
        "template": "arcface_128",
        "input_scale": 1.0 / 255.0,
        "input_mean": (0.0, 0.0, 0.0),
        "denorm_mult": 255.0,
        "denorm_add": 0.0,
        "mean": 0.0,
        "std": 255.0,
        "mapped": True,
        "mask_output": False,
    },
    "hyper1a": {
        "file": "swapper/hyperswap_1a_256.onnx",
        "crop": 256,
        "template": "arcface_128",
        "input_scale": 1.0 / 127.5,
        "input_mean": (127.5, 127.5, 127.5),
        "denorm_mult": 127.5,
        "denorm_add": 127.5,
        "mean": 0.5,
        "std": 0.5,
        "mapped": False,
        "mask_output": True,
    },
    "hyper1b": {
        "file": "swapper/hyperswap_1b_256.onnx",
        "crop": 256,
        "template": "arcface_128",
        "input_scale": 1.0 / 127.5,
        "input_mean": (127.5, 127.5, 127.5),
        "denorm_mult": 127.5,
        "denorm_add": 127.5,
        "mean": 0.5,
        "std": 0.5,
        "mapped": False,
        "mask_output": True,
    },
    "hyper1c": {
        "file": "swapper/hyperswap_1c_256.onnx",
        "crop": 256,
        "template": "arcface_128",
        "input_scale": 1.0 / 127.5,
        "input_mean": (127.5, 127.5, 127.5),
        "denorm_mult": 127.5,
        "denorm_add": 127.5,
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
    """Exact least-squares similarity transform with 3D-yaw aspect stabilization."""
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

    # Yaw robustness: in out-of-plane head turns (yaw), horizontal inter-ocular
    # distance contracts by cos(yaw), causing standard Umeyama scale to artificially
    # shrink and squash the face. Use the vertical axis (eye midpoint -> mouth midpoint)
    # as an invariant baseline under yaw.
    src_eye_mid = (src[0] + src[1]) * 0.5
    src_mouth_mid = (src[3] + src[4]) * 0.5
    src_vert = float(np.linalg.norm(src_mouth_mid - src_eye_mid))

    dst_eye_mid = (dst[0] + dst[1]) * 0.5
    dst_mouth_mid = (dst[3] + dst[4]) * 0.5
    dst_vert = float(np.linalg.norm(dst_mouth_mid - dst_eye_mid))

    if src_vert > 1e-4 and dst_vert > 1e-4:
        vert_scale = dst_vert / src_vert
        src_eye_dist = float(np.linalg.norm(src[1] - src[0]))
        dst_eye_dist = float(np.linalg.norm(dst[1] - dst[0]))
        canonical_ratio = dst_eye_dist / dst_vert
        current_ratio = src_eye_dist / src_vert
        # If horizontal distance foreshortened significantly relative to vertical (yaw > 30 deg)
        if current_ratio < 0.75 * canonical_ratio:
            scale = max(scale, vert_scale * 0.88)
        # Pitch robustness (nod down/up): vertical eye->mouth compresses while
        # eye distance stays. Fall back to the horizontal baseline so the
        # crop does not collapse and drop the face on a nod.
        elif current_ratio > 1.35 * canonical_ratio and src_eye_dist > 1e-4:
            horiz_scale = dst_eye_dist / src_eye_dist
            scale = max(scale, horiz_scale * 0.88)

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


def _decompose_similarity(matrix: np.ndarray) -> tuple[float, float, float, float]:
    """Decompose 2x3 affine similarity matrix into (tx, ty, scale, angle_rad)."""
    tx = float(matrix[0, 2])
    ty = float(matrix[1, 2])
    scale = float(math.sqrt(matrix[0, 0] ** 2 + matrix[1, 0] ** 2))
    angle = float(math.atan2(matrix[1, 0], matrix[0, 0]))
    return tx, ty, scale, angle


def _recompose_similarity(tx: float, ty: float, scale: float, angle: float) -> np.ndarray:
    """Reconstruct 2x3 affine similarity matrix from (tx, ty, scale, angle_rad)."""
    cos_a = math.cos(angle)
    sin_a = math.sin(angle)
    return np.array(
        [
            [scale * cos_a, -scale * sin_a, tx],
            [scale * sin_a, scale * cos_a, ty],
        ],
        dtype=np.float32,
    )


class _OneEuro:
    """Low-lag smoothing with dynamic dt support."""

    def __init__(
        self,
        freq: float = 30.0,
        min_cutoff: float = 1.0,
        beta: float = 0.02,
        d_cutoff: float = 1.0,
    ):
        self.freq = freq
        self.min_cutoff = min_cutoff
        self.beta = beta
        self.d_cutoff = d_cutoff
        self.x_prev: float | None = None
        self.dx_prev = 0.0
        self.last_time: float | None = None

    @staticmethod
    def _alpha(cutoff: float, freq: float) -> float:
        tau = 1.0 / (2.0 * math.pi * cutoff)
        return 1.0 / (1.0 + tau * freq)

    def __call__(self, x: float, timestamp: float | None = None) -> float:
        value = float(x)
        if self.x_prev is None:
            self.x_prev = value
            self.last_time = timestamp
            return value

        if timestamp is not None and self.last_time is not None:
            dt = timestamp - self.last_time
            freq = 1.0 / max(dt, 1e-4) if dt > 0 else self.freq
        else:
            freq = self.freq
        self.last_time = timestamp

        dx = (value - self.x_prev) * freq
        alpha_d = self._alpha(self.d_cutoff, freq)
        dx_hat = alpha_d * dx + (1.0 - alpha_d) * self.dx_prev
        cutoff = self.min_cutoff + self.beta * abs(dx_hat)
        alpha = self._alpha(cutoff, freq)
        estimate = alpha * value + (1.0 - alpha) * self.x_prev
        self.x_prev = estimate
        self.dx_prev = dx_hat
        return estimate

    def reset(self, x: float | None = None) -> None:
        self.x_prev = None if x is None else float(x)
        self.dx_prev = 0.0
        self.last_time = None


def _soft_box_mask(size: int, margin: int = 12) -> np.ndarray:
    """Distance-transform border feathering to prevent any rectangular box seams."""
    box = np.zeros((size, size), dtype=np.uint8)
    m = max(2, margin)
    box[m : size - m, m : size - m] = 255
    dist = cv2.distanceTransform(box, cv2.DIST_L2, 3)
    feather = np.clip(dist / float(m), 0.0, 1.0).astype(np.float32)
    blurred = cv2.GaussianBlur(feather, (0, 0), sigmaX=m / 3.0)
    blurred[0, :] = 0.0
    blurred[-1, :] = 0.0
    blurred[:, 0] = 0.0
    blurred[:, -1] = 0.0
    return blurred


def _face_oval(size: int, template: str = "arcface_112", grow: float = 1.0) -> np.ndarray:
    """Anatomical face mask covering forehead, temples, jawline and chin."""
    dst = _template_points(template, size)
    left_eye, right_eye = dst[0], dst[1]
    eye_dist = float(np.linalg.norm(right_eye - left_eye)) + 1e-6
    cx = float((left_eye[0] + right_eye[0]) * 0.5)
    cy = float((left_eye[1] + right_eye[1]) * 0.5 + eye_dist * 0.65)
    rx = eye_dist * 1.65 * grow  # widened from 1.45: covers side-cheek at yaw angles
    ry = eye_dist * 1.95 * grow
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    oval = (((xx - cx) / rx) ** 2 + ((yy - cy) / ry) ** 2) <= 1.0
    mask = np.zeros((size, size), dtype=np.float32)
    mask[oval] = 1.0
    kernel = max(5, int(round(size / 22.0)))
    if kernel % 2 == 0:
        kernel += 1
    mask = cv2.erode(mask, np.ones((kernel, kernel), np.uint8))
    blurred = cv2.GaussianBlur(mask, (0, 0), sigmaX=float(size) / 28.0)
    # Combine with crop boundary feathering to guarantee no edge clipping
    return blurred * _soft_box_mask(size, margin=max(6, int(size * 0.08)))


def paste_back(
    frame: np.ndarray,
    swapped: np.ndarray,
    transform: np.ndarray,
    extra_masks: list[np.ndarray] | None = None,
    template: str = "arcface_112",
) -> np.ndarray:
    """Composite a swapped crop back into the full frame with seamless boundary blending."""
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
    alpha = _face_oval(size, template)
    has_model_mask = False
    if extra_masks:
        for extra in extra_masks:
            if extra.shape[:2] != (size, size):
                extra = cv2.resize(extra, (size, size), interpolation=cv2.INTER_LINEAR)
            extra_f = np.clip(extra.astype(np.float32), 0.0, 1.0)
            # Ignore degenerate masks (all 0 / all 1): they carry no face
            # shape info and would either erase the swap or force it flat.
            if extra_f.mean() < 0.005 or extra_f.mean() > 0.995:
                continue
            has_model_mask = True
            alpha = alpha * extra_f
    if not has_model_mask:
        # No model/occluder guidance (e.g. inswapper with xseg off): keep a
        # soft face core so the swap holds together, but at reduced strength
        # so hair/background never turns into flat mask color.
        core = _face_oval(size, template, grow=0.72) * 0.85
        alpha = np.maximum(alpha * 0.9, core * 0.85)
    region_mask = cv2.warpAffine(
        alpha,
        local,
        (region_w, region_h),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=0.0,
    )
    region_mask = np.clip(region_mask, 0.0, 1.0)
    target = frame[y0:y1, x0:x1].astype(np.float32)
    if back.shape[:2] != target.shape[:2]:
        back = cv2.resize(back, (region_w, region_h), interpolation=cv2.INTER_LINEAR)
    weight = region_mask[..., None] if region_mask.ndim == 2 else region_mask
    merged = target * (1.0 - weight) + back.astype(np.float32) * weight
    output = frame.copy()
    output[y0:y1, x0:x1] = np.clip(merged, 0, 255).astype(np.uint8)
    return output


def _match_target_skin(
    swapped: np.ndarray,
    target_aligned: np.ndarray,
    amount: float = 1.0,
) -> np.ndarray:
    """Transfer colour cast from the live target face to the swapped crop.

    Strategy (chrominance-only Reinhard):
    - L (luminance): only correct gross exposure mismatch (max ±12 LAB units).
      Never scale L down — inswapper already outputs a naturally-lit face; dragging
      luminance to match a darker live face causes darkening and eye loss.
    - a, b (chrominance): full Reinhard transfer so skin hue / white-balance matches
      the live room environment without altering perceived brightness.
    """
    try:
        swapped_lab = cv2.cvtColor(swapped, cv2.COLOR_RGB2LAB).astype(np.float32)
        target_lab = cv2.cvtColor(target_aligned, cv2.COLOR_RGB2LAB).astype(np.float32)
        size = swapped.shape[0]

        # Central face sampling mask to ignore hair and background borders
        yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
        cx, cy = size * 0.5, size * 0.50   # centered vertically: captures eyes + cheeks
        rx, ry = size * 0.32, size * 0.44  # taller than before to include eye region
        mask = (((xx - cx) / rx) ** 2 + ((yy - cy) / ry) ** 2) <= 1.0

        swapped_pts = swapped_lab[mask]
        target_pts = target_lab[mask]

        swapped_mean = swapped_pts.mean(axis=0)
        swapped_std = swapped_pts.std(axis=0)
        target_mean = target_pts.mean(axis=0)
        target_std = target_pts.std(axis=0)

        matched = swapped_lab.copy()

        # L channel: gentle exposure correction only — clamp shift to ±12 LAB units.
        # This handles a studio-flash reference vs dim-room live face without
        # darkening the swap when the user has darker skin than the reference.
        l_shift = float(target_mean[0]) - float(swapped_mean[0])
        l_shift = float(np.clip(l_shift, -12.0, 12.0))
        matched[..., 0] = swapped_lab[..., 0] + l_shift

        # a, b channels: full Reinhard transfer to fix colour cast / white balance
        for c in (1, 2):
            s_std = float(swapped_std[c]) + 1e-4
            t_std = float(target_std[c]) + 1e-4
            scale_ratio = float(np.clip(t_std / s_std, 0.65, 1.5))
            matched[..., c] = (
                (swapped_lab[..., c] - float(swapped_mean[c])) * scale_ratio
                + float(target_mean[c])
            )

        matched_rgb = cv2.cvtColor(np.clip(matched, 0, 255).astype(np.uint8), cv2.COLOR_LAB2RGB)
        if amount >= 0.99:
            return matched_rgb
        blended = (
            swapped.astype(np.float32) * (1.0 - amount)
            + matched_rgb.astype(np.float32) * amount
        )
        return np.clip(blended, 0, 255).astype(np.uint8)
    except Exception as exc:
        logger.warning("target skin match skipped: %s", exc)
        return swapped


def _match_skin(
    swapped: np.ndarray,
    ref_mean: np.ndarray,
    ref_std: np.ndarray,
    amount: float | np.ndarray = 1.0,
) -> np.ndarray:
    """Pull the swapped crop's skin tone toward reference stats in LAB (legacy helper)."""
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
    pose_euro: list[_OneEuro] = field(default_factory=list)
    frame_index: int = 0
    prev_centroid: np.ndarray | None = None
    velocity: np.ndarray = field(default_factory=lambda: np.zeros(2, dtype=np.float32))
    last_speed: float = 0.0
    missed_frames: int = 0
    cached_crop: np.ndarray | None = None
    cached_masks: list[np.ndarray] = field(default_factory=list)
    cached_at: float = 0.0
    cached_mouth_open: float = 0.0
    frozen: np.ndarray | None = None
    frozen_at: float = 0.0
    last_seen: float = field(default_factory=time.perf_counter)

    raw_kps: np.ndarray | None = None          # unfiltered detector kps (used for extrapolation)

    def configure(self, profile_name: str) -> None:
        if self.budget is None or self.profile_name != profile_name:
            new_profile = profile_defaults(profile_name)
            old_fps = self.budget.target_neural_fps if self.budget is not None else None
            self.profile_name = profile_name
            self.budget = AdaptiveInferenceBudget(new_profile)
            # When upgrading to a higher-fps profile, seed from the last achieved
            # rate so we ramp gently rather than spiking into overload immediately.
            if old_fps is not None and old_fps < new_profile.neural_fps:
                self.budget.target_neural_fps = old_fps

    def reset_tracking(self) -> None:
        self.box = None
        self.kps = None
        self.raw_kps = None
        self.euro = []
        self.pose_euro = []
        self.prev_centroid = None
        self.velocity = np.zeros(2, dtype=np.float32)
        self.last_speed = 0.0
        self.missed_frames = 0


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
        self._swap_lock = Lock()   # serialises swapper + enhancer on the GPU stream
        self._meta_lock = Lock()   # serialises arcface + xseg (reference path, infrequent)
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
        detections = [r for r in self.scan_detector.detect(rgb) if r.kps is not None]
        if not detections:
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

        def _embed(crop: np.ndarray) -> np.ndarray | None:
            # RGB stays RGB: the models train on RGB, so no channel swap.
            blob = cv2.dnn.blobFromImage(
                crop, 1.0 / 127.5, (112, 112), (127.5, 127.5, 127.5), swapRB=False
            )
            try:
                with self._meta_lock:
                    vec = np.asarray(session.run(None, {meta.name: blob})[0]).reshape(-1)
            except Exception:
                return None
            n = float(np.linalg.norm(vec)) + 1e-9
            return (vec / n).astype(np.float32)

        # Pose-robust identity: average the frontal crop with mirror and
        # small rotations. One photo still works, but side/nod poses now
        # match the same person instead of dropping to a mask color.
        crops = [aligned]
        try:
            crops.append(cv2.flip(aligned, 1))
            center = (56.0, 56.0)
            for ang in (10.0, -10.0):
                rot = cv2.getRotationMatrix2D(center, ang, 1.0)
                crops.append(cv2.warpAffine(aligned, rot, (112, 112), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE))
        except Exception:
            pass
        vecs = [v for v in (_embed(c) for c in crops) if v is not None]
        if not vecs:
            self._source_ready = False
            return None
        embedding = np.mean(np.stack(vecs), axis=0).astype(np.float32)
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
        interior = _face_oval(112, "arcface_112") > 0.5
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
            with self._swap_lock:
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
            with self._meta_lock:
                out = np.asarray(session.run(None, {meta.name: blob})[0])
            mask = np.clip(out.reshape(256, 256), 0.0, 1.0).astype(np.float32)
            if mask.mean() < 0.005 or mask.mean() > 0.995:
                return None
            mask = cv2.GaussianBlur(mask, (0, 0), 5)
            # Remap [0.4, 1.0] → [0, 1] linearly: preserves soft edges in the 0.4–0.6
            # transition band where hand/object boundaries live.  Old formula
            # hard-zeroed everything below 0.5, creating a visible pixel step.
            mask = np.clip((mask - 0.4) / 0.6, 0.0, 1.0)
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

    def _filter_kps(
        self, context: SwapStream, fresh: np.ndarray, timestamp: float | None = None
    ) -> np.ndarray:
        flat = np.asarray(fresh, dtype=np.float32).reshape(-1)
        if len(context.euro) != flat.shape[0]:
            context.euro = [_OneEuro(min_cutoff=1.0, beta=0.02) for _ in range(flat.shape[0])]
        smoothed = np.array([f(value, timestamp) for f, value in zip(context.euro, flat)])
        return smoothed.reshape(5, 2).astype(np.float32)

    def _filter_transform(
        self, context: SwapStream, matrix: np.ndarray, timestamp: float
    ) -> np.ndarray:
        """Filter rigid similarity parameters (translation, scale, rotation) to prevent scale breathing.

        When the face is moving fast (context.last_speed > 10 px/frame) we bypass
        the filter and return the raw matrix directly — the jitter is imperceptible
        at that speed and the lag from a stiff filter causes the swap to slide behind
        the real face. At moderate speed (>5 px/frame) we use higher beta values to
        allow the filter to follow without over-smoothing.
        """
        speed = getattr(context, "last_speed", 0.0)

        # Fast motion: skip filter entirely — raw matrix tracks head perfectly
        if speed > 10.0:
            # Still feed values into the filters so they warm up for when we re-engage
            tx, ty, scale, angle = _decompose_similarity(matrix)
            if len(context.pose_euro) == 4:
                for f, v in zip(context.pose_euro, (tx, ty, scale, angle)):
                    f(v, timestamp)  # warm up, discard output
            return matrix

        tx, ty, scale, angle = _decompose_similarity(matrix)
        # Moderate motion: looser beta so the filter chases position quickly.
        # Scale beta raised (was 0.005): zoom-out steps must be followed
        # within a few frames or the swap visibly slides behind the face.
        beta_tx = 0.08 if speed > 5.0 else 0.03
        beta_scale = 0.05 if speed > 5.0 else 0.02
        beta_angle = 0.04 if speed > 5.0 else 0.01

        if len(context.pose_euro) != 4:
            context.pose_euro = [
                _OneEuro(min_cutoff=1.2, beta=beta_tx),    # tx
                _OneEuro(min_cutoff=1.2, beta=beta_tx),    # ty
                _OneEuro(min_cutoff=0.6, beta=beta_scale), # scale
                _OneEuro(min_cutoff=0.8, beta=beta_angle), # angle
            ]
        else:
            # Hot-swap beta on running filters — _OneEuro stores beta as self.beta
            # but never reads it dynamically; mutating it is the only way to adapt.
            context.pose_euro[0].beta = beta_tx
            context.pose_euro[1].beta = beta_tx
            context.pose_euro[2].beta = beta_scale
            context.pose_euro[3].beta = beta_angle
        s_tx = context.pose_euro[0](tx, timestamp)
        s_ty = context.pose_euro[1](ty, timestamp)
        s_scale = context.pose_euro[2](scale, timestamp)
        s_angle = context.pose_euro[3](angle, timestamp)
        return _recompose_similarity(s_tx, s_ty, s_scale, s_angle)

    def _track(self, rgb: np.ndarray, context: SwapStream, now: float) -> bool:
        """Fast ROI re-detection when tracking, with full-frame fallback scanner.

        Tracking within an expanded ROI drops detector time from ~14ms to ~2.5ms,
        preserving the 30 fps budget. Full scan re-acquires instantly on track loss.
        """
        started = time.perf_counter()
        h, w = rgb.shape[:2]
        found = []

        def _with_box_kps(region: Region, dx: int = 0, dy: int = 0) -> Region:
            # Box-fallback keypoints: pitch-down / profile faces often come
            # back with a box but no 5-point kps. Estimate ArcFace-order
            # points from box geometry so tracking never drops the face.
            if region.kps is not None:
                return region
            bw = float(max(1, region.width))
            bh = float(max(1, region.height))
            pts = (
                (region.x + bw * 0.35 + dx, region.y + bh * 0.40 + dy),
                (region.x + bw * 0.65 + dx, region.y + bh * 0.40 + dy),
                (region.x + bw * 0.50 + dx, region.y + bh * 0.55 + dy),
                (region.x + bw * 0.38 + dx, region.y + bh * 0.75 + dy),
                (region.x + bw * 0.62 + dx, region.y + bh * 0.75 + dy),
            )
            return Region(region.x + dx, region.y + dy, region.width, region.height, region.score * 0.9, pts)

        # Fast ROI path: if already tracking, detect inside a padded sub-window.
        # Generous pad (60% + extra vertical) keeps nod-down and zoom steps
        # inside the window; small faces (zoom-out) are allowed down to 32px.
        if context.box is not None and context.missed_frames == 0:
            # Region uses x, y, width, height — compute corners from those fields
            bx1, by1 = context.box.x, context.box.y
            bx2, by2 = context.box.x + context.box.width, context.box.y + context.box.height
            bw, bh = bx2 - bx1, by2 - by1
            pad_x, pad_y = int(bw * 0.60), int(bh * 0.70)
            rx1, ry1 = max(0, bx1 - pad_x), max(0, by1 - pad_y)
            rx2, ry2 = min(w, bx2 + pad_x), min(h, by2 + pad_y)
            if (rx2 - rx1) >= 32 and (ry2 - ry1) >= 32:
                roi = rgb[ry1:ry2, rx1:rx2]
                try:
                    roi_found = list(self.scan_detector.detect(roi))
                    if roi_found:
                        roi_found = [_with_box_kps(r) for r in roi_found]
                        best_roi = max(roi_found, key=lambda r: (r.score, r.area))
                        if best_roi.score >= ROI_SCORE and best_roi.kps is not None:
                            # Translate ROI-local coords back to full-frame coords.
                            found = [
                                Region(
                                    best_roi.x + rx1,
                                    best_roi.y + ry1,
                                    best_roi.width,
                                    best_roi.height,
                                    best_roi.score,
                                    tuple((p[0] + rx1, p[1] + ry1) for p in best_roi.kps),
                                )
                            ]
                except Exception as exc:
                    logger.debug("ROI detection skipped: %s", exc)

        # Full scan fallback if ROI was not attempted or did not detect
        if not found:
            try:
                scanned = list(self.scan_detector.detect(rgb))
                found = [_with_box_kps(r) for r in scanned if r.kps is not None or True]
                found = [r for r in found if r.kps is not None]
            except Exception as exc:
                logger.warning("swap tracking failed: %s", exc)
                return False

        if not found:
            detector_window.add((time.perf_counter() - started) * 1000.0)
            return False

        best = max(found, key=lambda r: (r.score, r.area))
        # Small/zoomed-out faces are genuinely tiny — never drop them for size.
        # Only guard against noise boxes below MIN_FACE_PX on acquisition.
        if context.box is None:
            has_real_kps = best.score >= ACQUIRE_KPS_SCORE
            too_small = max(best.width, best.height) < MIN_FACE_PX
            if not has_real_kps or too_small:
                # One last chance: box-only hits need the strict threshold.
                if best.score < settings.face_detector_confidence:
                    detector_window.add((time.perf_counter() - started) * 1000.0)
                    return False

        context.box = best
        fresh = np.asarray(best.kps, dtype=np.float32).reshape(5, 2)
        centroid = fresh.mean(axis=0)
        if context.prev_centroid is not None:
            disp = centroid - context.prev_centroid
            context.last_speed = float(np.linalg.norm(disp))
            # Smooth velocity vector (EMA) for graceful trajectory extrapolation
            context.velocity = 0.7 * disp.astype(np.float32) + 0.3 * context.velocity
        context.prev_centroid = centroid.astype(np.float32)
        if context.kps is None:
            context.euro = []
            context.pose_euro = []
        context.raw_kps = fresh.copy()   # save unfiltered for extrapolation in _no_face
        context.kps = self._filter_kps(context, fresh, now)
        context.missed_frames = 0
        detector_window.add((time.perf_counter() - started) * 1000.0)
        return True

    def _swap_crop(
        self, aligned: np.ndarray, latent: np.ndarray, size: int
    ) -> tuple[np.ndarray | None, list[np.ndarray]]:
        session = self.swapper.load()
        if session is None:
            return None, []
        input_scale = float(self.spec.get("input_scale", 1.0 / self.spec.get("std", 255.0)))
        input_mean = tuple(self.spec.get("input_mean", (self.spec.get("mean", 0.0),) * 3))
        blob = cv2.dnn.blobFromImage(
            aligned,
            input_scale,
            (size, size),
            input_mean,
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
        with self._swap_lock:
            results = session.run(None, feeds)
        outputs = session.get_outputs()
        decoded = None
        masks: list[np.ndarray] = []
        denorm_mult = float(self.spec.get("denorm_mult", self.spec.get("std", 255.0)))
        denorm_add = float(self.spec.get("denorm_add", self.spec.get("mean", 0.0) * 255.0))
        for meta, value in zip(outputs, results):
            array = np.asarray(value)
            shape = list(meta.shape)
            channels = shape[1] if len(shape) == 4 else 0
            if array.ndim == 4 and channels == 1:
                masks.append(np.clip(array[0, 0], 0.0, 1.0).astype(np.float32))
                continue
            if decoded is not None:
                continue
            # Only a real 3-channel image is a face. A mask mistaken for a
            # face is exactly the "flat facemask color" bug, so verify.
            try:
                img4 = array[0]
                if img4.ndim != 3 or img4.shape[0] not in (3, 4):
                    continue
                image = img4.transpose(1, 2, 0)[..., :3] * denorm_mult + denorm_add
                image = np.nan_to_num(image, nan=128.0, posinf=255.0, neginf=0.0)
                # Model output is RGB, matching our frames: no channel flip.
                cand = np.clip(image, 0.0, 255.0).astype(np.uint8)
                # Reject flat-mask decodes: a real face always has variance.
                if float(cand.std()) < 4.0:
                    continue
                decoded = cand
            except Exception:
                continue
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

        tracked = self._track(rgb, context, now)
        if not tracked or context.kps is None:
            return self._no_face(rgb, context, width, height, now)

        # Mouth dynamics check: force neural inference on speech / phoneme transitions
        mouth_open = float(np.linalg.norm(context.kps[4] - context.kps[3]))
        mouth_delta = abs(mouth_open - context.cached_mouth_open) / max(mouth_open, 1e-4)
        force_mouth_inference = mouth_delta > 0.18

        if not budget.should_infer(now) and not force_mouth_inference:
            dropped_inference_counter.increment()
            if context.cached_crop is None or not self._cache_valid(context, now):
                return self._reuse_or_hold(rgb, context, width, height, now)
            return self._paste_cached(rgb, context, width, height, now)

        aligned, transform = norm_crop(rgb, context.kps, size, template)
        if aligned is None or transform is None:
            return self._no_face(rgb, context, width, height, now)
        smooth_transform = self._filter_transform(context, transform, now)

        overload_before = budget.overload_events
        started = time.perf_counter()
        try:
            swapped, model_masks = self._swap_crop(aligned, latent, size)
            if swapped is None:
                raise RuntimeError("swapper produced no face")
            # Pro lighting rule: match the LIVE room light (chrominance-only,
            # luminance clamped) so the swap never looks like flat mask paint.
            # The reference identity stays in the swapper output itself; only
            # the light color is borrowed from the current frame.
            try:
                swapped = _match_target_skin(swapped, aligned, amount=1.0)
            except Exception as exc:
                logger.debug("live skin match skipped: %s", exc)
            swapped = self._enhance(swapped, size)

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
            output = paste_back(rgb, swapped, smooth_transform, masks or None, template)
        except cv2.error as exc:
            inference_error_counter.increment()
            logger.warning("face-swap paste-back failed: %s", exc)
            return self._reuse_or_hold(rgb, context, width, height, now)
        context.cached_crop = swapped
        context.cached_masks = masks
        context.cached_at = now
        context.cached_mouth_open = mouth_open
        context.frozen = output
        context.frozen_at = now
        pipeline_health.update(
            face_swap_ready=True,
            source_ready=True,
            target_neural_fps=round(budget.target_neural_fps, 2),
        )
        return output

    def _paste_cached(
        self, rgb: np.ndarray, context: SwapStream, width: int, height: int, now: float
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
        smooth_current = self._filter_transform(context, current, now)
        try:
            return paste_back(
                rgb, context.cached_crop, smooth_current, context.cached_masks or None, template
            )
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
            return self._paste_cached(rgb, context, width, height, now)
        return self._no_face(rgb, context, width, height, now)

    def _no_face(
        self, rgb: np.ndarray, context: SwapStream, width: int, height: int, now: float
    ) -> np.ndarray:
        pipeline_health.update(face_swap_ready=True, source_ready=True)
        context.missed_frames += 1
        size = int(self.spec["crop"])
        template = str(self.spec["template"])

        # Motion hold for brief detector gaps (nod, zoom step, blink):
        # extrapolate along velocity for a few frames so the swap rides the
        # head instead of flashing the real face.
        if (
            context.cached_crop is not None
            and context.kps is not None
            and context.missed_frames <= HOLD_FRAMES
            and context.last_speed <= STILL_SPEED_PX
            and (now - context.cached_at) <= STILL_FREEZE_SECONDS
        ):
            # Use raw (unfiltered) kps as extrapolation base: context.kps is One Euro
            # smoothed, so extrapolating from it compounds the filter lag on top of the
            # missed-frame offset. raw_kps is the direct SCRFD output — accurate position.
            base_kps = context.raw_kps if context.raw_kps is not None else context.kps
            extrapolated_kps = base_kps + context.velocity * float(context.missed_frames)
            current = _similarity(extrapolated_kps, _template_points(template, size))
            if current is not None:
                cache_reuse_counter.increment()
                try:
                    return paste_back(
                        rgb, context.cached_crop, current, context.cached_masks or None, template
                    )
                except cv2.error:
                    pass

        # Stationary freeze fallback for gentle blinks
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

    def warmup(self) -> None:
        """Pre-warm CUDA execution providers with dummy passes so live video has 0ms warmup lag."""
        try:
            logger.info("pre-warming face swap GPU kernels...")
            dummy_img = np.full((128, 128, 3), 128, dtype=np.uint8)
            try:
                self.scan_detector.detect(dummy_img)
            except Exception:
                pass
            arc_session = self.arcface.load()
            if arc_session is not None:
                meta = arc_session.get_inputs()[0]
                dummy_arc = np.zeros((1, 3, 112, 112), dtype=np.float32)
                with self._meta_lock:
                    arc_session.run(None, {meta.name: dummy_arc})
            swap_session = self.swapper.load()
            if swap_session is not None:
                inputs = swap_session.get_inputs()
                target_meta = next(
                    (m for m in inputs if "target" in m.name.lower()), inputs[0]
                )
                source_meta = next(
                    (m for m in inputs if m.name != target_meta.name), None
                )
                crop_size = int(self.spec.get("crop", 128))
                dummy_target = np.zeros((1, 3, crop_size, crop_size), dtype=np.float32)
                feeds = {target_meta.name: dummy_target}
                if source_meta is not None:
                    dummy_latent = np.zeros((1, 512), dtype=np.float32)
                    feeds[source_meta.name] = dummy_latent
                with self._swap_lock:
                    swap_session.run(None, feeds)
            logger.info("face swap GPU kernels pre-warmed successfully")
        except Exception as exc:
            logger.warning("face swap pre-warm failed: %s", exc)

    def preload(self) -> dict:
        self.swapper.load()
        self.arcface.load()
        if self.enhancer_enabled:
            self.enhancer.load()
        if self.occluder_enabled:
            self.occluder.load()
        if self.spec["mapped"]:
            self._emap_matrix()
        self.warmup()
        pipeline_health.update(
            face_swap_ready=self.available,
            swapper_backend=self.swapper.providers[0] if self.swapper.providers else "none",
        )
        return self.status()


engine = FaceSwapEngine()
