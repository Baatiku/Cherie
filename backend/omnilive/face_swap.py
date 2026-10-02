"""Real-time identity face swap (InsightFace inswapper family).

Unlike the generic image-to-image adapter, a real swapper needs an
identity vector, not a second photo: the source face is encoded once with
ArcFace into a 512-d embedding, mapped through the swapper's own embedding
matrix, and then applied to every live frame. This module implements that
exact contract:

- detect (SCRFD with keypoints) -> 5-point ArcFace alignment (128px)
- ArcFace ResNet50 embedding of the session reference photo (once per photo)
- inswapper forward: aligned target crop + mapped source latent
- inverse-warp paste-back with a feathered face mask

Without the weight files (or without a session reference photo) every entry
point degrades to untouched pass-through so transport stays testable.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock

import cv2
import numpy as np

from .config import settings
from .models import QualityProfile
from .regions import LandmarkRegionTracker, Region
from .session import registry
from .telemetry import (
    cache_reuse_counter,
    dropped_inference_counter,
    inference_error_counter,
    model_window,
    overload_event_counter,
    pipeline_health,
    tracker_window,
    tracking_reset_counter,
)
from .temporal import AdaptiveInferenceBudget, profile_defaults
from .vision import build_region_detector, resolve_vision_model_path

logger = logging.getLogger("omnilive.faceswap")

# How long a swapped frame may be held on screen while tracking briefly
# loses the face, instead of flashing the real face. Longer than the
# detector gap (~200-400ms) but short enough that a truly gone face
# clears quickly.
SWAP_HOLD_SECONDS = 0.8

# ArcFace canonical 5-point template at 112px (left eye, right eye, nose,
# mouth-left, mouth-right). Scaled to the requested crop size.
_ARCFACE_DST_112 = np.array(
    [
        [38.2946, 51.6963],
        [73.5318, 51.5014],
        [56.0252, 71.7366],
        [41.5493, 92.3655],
        [70.7299, 92.2041],
    ],
    dtype=np.float32,
)


def _similarity(src5: np.ndarray, dst5: np.ndarray) -> np.ndarray | None:
    """Exact least-squares similarity transform (Umeyama, no RANSAC).

    Estimates scale + rotation + translation from all 5 points at once.
    RANSAC-based estimators can lock onto a degenerate 3-point subset and
    tilt the whole alignment, which pastes the swapped mouth in the wrong
    place (the classic "two mouths" artifact).
    """
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


def norm_crop(img: np.ndarray, kps: np.ndarray, image_size: int = 128):
    """Align a face to the ArcFace template (InsightFace norm_crop2)."""
    dst = (_ARCFACE_DST_112 * (float(image_size) / 112.0)).astype(np.float32)
    transform = _similarity(np.asarray(kps, dtype=np.float32).reshape(5, 2), dst)
    if transform is None:
        return None, None
    warped = cv2.warpAffine(
        img,
        transform,
        (image_size, image_size),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=0.0,
    )
    return warped, transform


def paste_back(
    frame: np.ndarray,
    swapped: np.ndarray,
    transform: np.ndarray,
    raw: np.ndarray | None = None,
    aligned_orig: np.ndarray | None = None,
) -> np.ndarray:
    """Composite a swapped 128px crop back into the full frame.

    InsightFace-style change masking: only pixels the swap actually
    changed get pasted. Unchanged areas (and the target's own mouth
    interior, which the swapper preserves) stay exactly as the camera
    saw them, so a small alignment wobble can never print a second
    mouth next to the real one.
    """
    height, width = frame.shape[:2]
    size = int(swapped.shape[0])
    inverse = cv2.invertAffineTransform(transform)
    back = cv2.warpAffine(
        swapped,
        inverse,
        (width, height),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=0.0,
    )
    if raw is not None and aligned_orig is not None:
        diff_source, diff_target = raw, aligned_orig
    else:
        diff_source, diff_target = swapped, cv2.warpAffine(
            frame,
            transform,
            (size, size),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=0.0,
        )
    diff = np.abs(
        diff_source.astype(np.float32) - diff_target.astype(np.float32)
    ).mean(axis=2)
    diff[:2, :] = 0
    diff[-2:, :] = 0
    diff[:, :2] = 0
    diff[:, -2:] = 0
    white = np.full((size, size), 255, dtype=np.float32)
    face_mask = cv2.warpAffine(
        white,
        inverse,
        (width, height),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=0.0,
    )
    face_mask[face_mask > 20.0] = 255.0
    ys, xs = np.where(face_mask == 255.0)
    if len(xs) > 0:
        extent = int(max(xs.max() - xs.min(), ys.max() - ys.min()))
        kernel = max(int(round(extent / 12.0)), 7)
    else:
        kernel = 11
    if kernel % 2 == 0:
        kernel += 1
    face_mask = cv2.erode(face_mask, np.ones((kernel, kernel), np.uint8))
    change = cv2.warpAffine(
        diff,
        inverse,
        (width, height),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=0.0,
    )
    change[change < 10.0] = 0.0
    change[change >= 10.0] = 255.0
    change = cv2.dilate(change, np.ones((3, 3), np.uint8))
    smooth = max(kernel // 2, 5)
    if smooth % 2 == 0:
        smooth += 1
    face_mask = cv2.GaussianBlur(face_mask, (0, 0), sigmaX=float(smooth))
    change = cv2.GaussianBlur(change, (0, 0), sigmaX=5.0)
    alpha = (
        np.clip(face_mask, 0.0, 255.0) / 255.0
        * np.clip(change, 0.0, 255.0) / 255.0
    )[..., None]
    merged = frame.astype(np.float32) * (1.0 - alpha) + back.astype(np.float32) * alpha
    return np.clip(merged, 0, 255).astype(np.uint8)


def _match_skin(
    swapped: np.ndarray, ref_mean: np.ndarray, ref_std: np.ndarray, amount: float = 1.0
) -> np.ndarray:
    """Pull the swapped crop's skin tone toward the reference photo in LAB space.

    LAB separates brightness from color, and the statistics come from the
    reference face itself, so the swapped face carries the photo's skin
    tone instead of the live camera lighting.
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
        blended = (
            swapped.astype(np.float32) * (1.0 - amount)
            + matched_rgb.astype(np.float32) * amount
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
    tracker: LandmarkRegionTracker = field(default_factory=LandmarkRegionTracker)
    budget: AdaptiveInferenceBudget | None = None
    frame_index: int = 0
    last_detection_frame: int = -10_000
    kps: np.ndarray | None = None
    cached: np.ndarray | None = None
    cached_at: float = 0.0
    last_seen: float = field(default_factory=time.perf_counter)

    def configure(self, profile_name: str) -> None:
        if self.budget is None or self.profile_name != profile_name:
            self.profile_name = profile_name
            self.budget = AdaptiveInferenceBudget(profile_defaults(profile_name))


class FaceSwapEngine:
    """Session-reference-driven real-time face swapper."""

    requires_reference = True

    def __init__(self, detector=None):
        self.detector = detector or build_region_detector(max_regions=1)
        self.swapper = _LazySwapSession(settings.swapper_model)
        self.arcface = _LazySwapSession(settings.arcface_model)
        self.enhancer = _LazySwapSession(settings.enhancer_model)
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

    def _enhance(self, rgb128: np.ndarray) -> np.ndarray:
        """Optional detail pass (GPEN blind restoration). Never breaks a frame."""
        if not self.enhancer_enabled:
            return rgb128
        try:
            session = self.enhancer.load()
            if session is None:
                return rgb128
            up = cv2.resize(rgb128, (256, 256), interpolation=cv2.INTER_CUBIC)
            blob = (up.astype(np.float32) / 127.5 - 1.0).transpose(2, 0, 1)[None, ...]
            meta = session.get_inputs()[0]
            with self._infer_lock:
                out = np.asarray(session.run(None, {meta.name: blob})[0])
            enhanced = np.clip((out[0].transpose(1, 2, 0) + 1.0) * 127.5, 0, 255)
            if not np.isfinite(float(enhanced.mean())):
                return rgb128
            if float(enhanced.mean()) < 5.0 or float(enhanced.mean()) > 250.0:
                return rgb128
            return cv2.resize(enhanced.astype(np.uint8), (128, 128), interpolation=cv2.INTER_AREA)
        except Exception as exc:
            logger.warning("face enhancer skipped: %s", exc)
            return rgb128

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
            return cached["latent"]
        detections = [r for r in self.detector.detect(rgb) if r.kps is not None]
        if not detections:
            logger.warning("no alignable face in reference image for stream %s", stream_key)
            self._source_ready = False
            return None
        best = max(detections, key=lambda r: r.area)
        kps = np.asarray(best.kps, dtype=np.float32).reshape(5, 2)
        aligned, _ = norm_crop(rgb, kps, 112)
        if aligned is None:
            self._source_ready = False
            return None
        session = self.arcface.load()
        if session is None:
            self._source_ready = False
            return None
        meta = session.get_inputs()[0]
        blob = cv2.dnn.blobFromImage(
            aligned, 1.0 / 127.5, (112, 112), (127.5, 127.5, 127.5), swapRB=True
        )
        with self._infer_lock:
            embedding = np.asarray(session.run(None, {meta.name: blob})[0]).reshape(-1)
        norm = float(np.linalg.norm(embedding)) + 1e-9
        embedding = embedding / norm
        emap = self._emap_matrix()
        if emap is None:
            self._source_ready = False
            return None
        latent = embedding.reshape(1, -1) @ emap
        latent = latent / (float(np.linalg.norm(latent)) + 1e-9)
        latent = latent.astype(np.float32)
        tone_lab = cv2.cvtColor(aligned, cv2.COLOR_RGB2LAB).astype(np.float32)
        tone_mean = tone_lab.reshape(-1, 3).mean(axis=0).astype(np.float32)
        tone_std = tone_lab.reshape(-1, 3).std(axis=0).astype(np.float32)
        with self._lock:
            self._sources[sha] = {
                "latent": latent,
                "tone_mean": tone_mean,
                "tone_std": tone_std,
            }
            self._stream_sha[stream_key] = sha
            if len(self._sources) > 16:
                oldest = next(iter(self._sources))
                self._sources.pop(oldest, None)
        self._source_ready = True
        logger.info("face-swap identity ready for stream %s", stream_key)
        return latent

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

    def _move_kps(self, context: SwapStream) -> None:
        transform = context.tracker.last_transform
        if context.kps is None or transform is None:
            return
        moved = cv2.transform(
            np.asarray(context.kps, dtype=np.float32).reshape(-1, 1, 2),
            transform,
        ).reshape(-1, 2)
        context.kps = moved.astype(np.float32)

    def _cache_valid(self, context: SwapStream, now: float) -> bool:
        if context.cached is None or context.tracker.region is None:
            return False
        if context.tracker.confidence < settings.tracker_min_confidence:
            return False
        ttl_ms = profile_defaults(context.profile_name).cache_ttl_ms
        return (now - context.cached_at) * 1000.0 <= ttl_ms

    def _held_frame(
        self, context: SwapStream, width: int, height: int, now: float
    ) -> np.ndarray | None:
        """Last swapped frame, briefly held while tracking is lost.

        Returning this instead of the untouched camera frame stops the
        real face flashing through on every detector/tracker gap.
        """
        if context.cached is None:
            return None
        if (now - context.cached_at) > SWAP_HOLD_SECONDS:
            return None
        cache_reuse_counter.increment()
        transform = context.tracker.last_transform
        if transform is not None:
            try:
                return cv2.warpAffine(
                    context.cached, transform, (width, height), flags=cv2.INTER_LINEAR
                )
            except cv2.error:
                pass
        return context.cached

    @staticmethod
    def _near_still(transform: np.ndarray | None) -> bool:
        """True when the face barely moved: reuse the cached frame untouched.

        Warping by a sub-pixel transform every frame makes edges shimmer;
        a still face gets the identical array back (zero wobble).
        """
        if transform is None:
            return True
        tx, ty = float(transform[0, 2]), float(transform[1, 2])
        scale = float(np.linalg.norm(transform[:, :2]) / np.sqrt(2.0))
        return abs(tx) < 0.75 and abs(ty) < 0.75 and abs(scale - 1.0) < 0.01

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

        if context.tracker.region is not None:
            started = time.perf_counter()
            tracked = context.tracker.update(rgb)
            tracker_window.add((time.perf_counter() - started) * 1000.0)
            if tracked is None:
                tracking_reset_counter.increment()
                context.kps = None
            else:
                self._move_kps(context)

        frames_since_detection = context.frame_index - context.last_detection_frame
        detector_due = (
            frames_since_detection >= budget.detector_interval
            or context.kps is None
            or (
                context.tracker.region is not None
                and context.tracker.confidence < settings.tracker_min_confidence
                and frames_since_detection >= 2
            )
        )
        if detector_due:
            detections = [r for r in self.detector.detect(rgb) if r.kps is not None]
            context.last_detection_frame = context.frame_index
            if detections:
                best = max(detections, key=lambda r: (r.score, r.area))
                try:
                    context.tracker.seed(rgb, best)
                    context.kps = np.asarray(best.kps, dtype=np.float32).reshape(5, 2)
                except ValueError:
                    context.kps = None
            elif context.kps is None or context.tracker.confidence < settings.tracker_min_confidence:
                context.tracker.reset()
                context.kps = None

        if context.kps is None:
            pipeline_health.update(face_swap_ready=True, source_ready=True)
            held = self._held_frame(context, width, height, now)
            return held if held is not None else rgb

        if not budget.should_infer(now):
            dropped_inference_counter.increment()
            if not self._cache_valid(context, now):
                held = self._held_frame(context, width, height, now)
                return held if held is not None else rgb
            cache_reuse_counter.increment()
            cached = context.cached
            assert cached is not None
            transform = context.tracker.last_transform
            if self._near_still(transform):
                return cached
            if transform is not None:
                try:
                    return cv2.warpAffine(cached, transform, (width, height), flags=cv2.INTER_LINEAR)
                except cv2.error:
                    return cached
            return cached

        session = self.swapper.load()
        if session is None:
            return rgb
        aligned, transform = norm_crop(rgb, context.kps, 128)
        if aligned is None or transform is None:
            return rgb
        blob = cv2.dnn.blobFromImage(aligned, 1.0 / 255.0, (128, 128), (0, 0, 0), swapRB=True)
        inputs = session.get_inputs()
        overload_before = budget.overload_events
        started = time.perf_counter()
        try:
            with self._infer_lock:
                pred = np.asarray(
                    session.run(None, {inputs[0].name: blob, inputs[1].name: latent})[0]
                )
            swap_ms = (time.perf_counter() - started) * 1000.0
        except Exception as exc:
            inference_error_counter.increment()
            logger.warning("face-swap inference failed: %s", exc)
            held = self._held_frame(context, width, height, now)
            return held if held is not None else rgb

        swapped = np.clip(pred.transpose((0, 2, 3, 1))[0] * 255.0, 0, 255).astype(np.uint8)
        swapped = swapped[:, :, ::-1]
        swapped_raw = swapped
        swapped = self._enhance(swapped)
        tone = self.source_tone(stream_key)
        if tone is not None:
            swapped = _match_skin(swapped, tone[0], tone[1])
        neural_ms = (time.perf_counter() - started) * 1000.0
        model_window.add(neural_ms)
        budget.observe_inference(neural_ms)
        if budget.overload_events > overload_before:
            overload_event_counter.increment(budget.overload_events - overload_before)
        try:
            output = paste_back(
                rgb, swapped, transform, raw=swapped_raw, aligned_orig=aligned
            )
        except cv2.error as exc:
            inference_error_counter.increment()
            logger.warning("face-swap paste-back failed: %s", exc)
            held = self._held_frame(context, width, height, now)
            return held if held is not None else rgb
        context.cached = output
        context.cached_at = now
        pipeline_health.update(
            face_swap_ready=True,
            source_ready=True,
            target_neural_fps=round(budget.target_neural_fps, 2),
            tracking_confidence=round(context.tracker.confidence, 3),
        )
        return output

    def status(self) -> dict:
        return {
            "face_swap_available": self.available,
            "face_swap_source_ready": self._source_ready,
            "swapper_model": self.swapper.status(),
            "arcface_model": self.arcface.status(),
            "enhancer_model": self.enhancer.status(),
            "enhancer_enabled": self.enhancer_enabled,
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
        self._emap_matrix()
        pipeline_health.update(
            face_swap_ready=self.available,
            swapper_backend=self.swapper.providers[0] if self.swapper.providers else "none",
        )
        return self.status()


engine = FaceSwapEngine()
