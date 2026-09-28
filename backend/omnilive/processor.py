from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
import time

from av import VideoFrame
import cv2
import numpy as np

from .config import settings
from .model_runtime import runtime
from .models import EffectProfile, QualityProfile
from .regions import LandmarkRegionTracker, OpenCvHaarFaceDetector, Region, RegionDetector
from .telemetry import (
    cache_reuse_counter,
    compositor_window,
    detector_window,
    dropped_inference_counter,
    inference_error_counter,
    landmark_window,
    model_window,
    overload_event_counter,
    pipeline_health,
    processor_window,
    tracker_window,
    tracking_reset_counter,
)
from .temporal import AdaptiveInferenceBudget, profile_defaults
from .vision import (
    SemanticLandmarks,
    align_face,
    build_landmark_provider,
    build_person_detector,
    build_region_detector,
    vision_status as vision_provider_status,
)


@dataclass
class ProcessingTrace:
    total_ms: float = 0.0
    model_ms: float | None = None
    model_input_width: int = 0
    model_input_height: int = 0
    inference_attempted: bool = False
    inference_succeeded: bool = False
    inference_error: bool = False
    target_neural_fps: float = 0.0
    detector_interval: int = 0
    crop_max_side: int = 0
    overload_events: int = 0


class VisualProcessor:
    def process_rgb(self, rgb: np.ndarray, effect: EffectProfile) -> np.ndarray:
        if effect == EffectProfile.ORIGINAL:
            return rgb
        if effect == EffectProfile.MONOCHROME:
            gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
            return np.repeat(gray[..., None], 3, axis=2)
        if effect == EffectProfile.SOFT_FOCUS:
            blurred = cv2.GaussianBlur(rgb, (0, 0), sigmaX=2.0, sigmaY=2.0)
            return cv2.addWeighted(rgb, 0.72, blurred, 0.28, 0)
        if effect == EffectProfile.PIXELATE:
            h, w = rgb.shape[:2]
            sw = max(16, w // 18)
            sh = max(16, h // 18)
            small = cv2.resize(rgb, (sw, sh), interpolation=cv2.INTER_LINEAR)
            return cv2.resize(small, (w, h), interpolation=cv2.INTER_NEAREST)
        if effect == EffectProfile.CUSTOM_ONNX:
            return runtime.run_image(rgb)
        return rgb

    async def process(self, frame: VideoFrame, effect: EffectProfile) -> VideoFrame:
        if effect == EffectProfile.ORIGINAL:
            return frame
        rgb = frame.to_ndarray(format="rgb24")
        out = self.process_rgb(rgb, effect)
        return _video_frame(out, frame)


@dataclass
class StreamContext:
    profile_name: str
    tracker: LandmarkRegionTracker = field(default_factory=LandmarkRegionTracker)
    budget: AdaptiveInferenceBudget | None = None
    frame_index: int = 0
    last_detection_frame: int = -10_000
    cached_transform: np.ndarray | None = None
    cached_at: float = 0.0
    semantic_landmarks: SemanticLandmarks | None = None
    last_seen: float = field(default_factory=time.perf_counter)

    def configure(self, profile_name: str) -> None:
        if self.budget is None or self.profile_name != profile_name:
            self.profile_name = profile_name
            self.budget = AdaptiveInferenceBudget(profile_defaults(profile_name))

    def clear_cache(self) -> None:
        self.cached_transform = None
        self.cached_at = 0.0


class LocalizedTemporalProcessor:
    def __init__(
        self,
        detector: RegionDetector | None = None,
        visual: VisualProcessor | None = None,
        landmark_provider=None,
    ):
        self.detector = detector or build_region_detector(max_regions=1)
        self.landmark_provider = landmark_provider if landmark_provider is not None else build_landmark_provider()
        self.person_detector = build_person_detector()
        self.visual = visual or VisualProcessor()
        self._contexts: dict[str, StreamContext] = {}
        self._contexts_lock = Lock()
        pipeline_health.update(
            detector_backend=self.detector.name,
            tracker_backend=LandmarkRegionTracker.name,
            landmark_backend=getattr(self.landmark_provider, "name", "none"),
            face_alignment_enabled=settings.face_alignment_enabled,
        )

    def vision_status(self) -> dict:
        return vision_provider_status(self.detector, self.landmark_provider, self.person_detector)

    def _refresh_landmarks(self, context: StreamContext, rgb: np.ndarray, region: Region) -> None:
        if self.landmark_provider is None:
            context.semantic_landmarks = None
            pipeline_health.update(landmark_points=0)
            return
        started = time.perf_counter()
        try:
            context.semantic_landmarks = self.landmark_provider.detect(rgb, region)
        except Exception:
            context.semantic_landmarks = None
        finally:
            landmark_window.add((time.perf_counter() - started) * 1000.0)
        pipeline_health.update(
            landmark_points=0 if context.semantic_landmarks is None else context.semantic_landmarks.count
        )

    def _move_landmarks_with_tracker(self, context: StreamContext) -> None:
        landmarks = context.semantic_landmarks
        transform = context.tracker.last_transform
        if landmarks is None or transform is None:
            return
        moved = cv2.transform(
            landmarks.points[:, :2].astype(np.float32).reshape(-1, 1, 2),
            transform,
        ).reshape(-1, 2)
        context.semantic_landmarks = SemanticLandmarks(
            points=moved,
            score=landmarks.score,
            schema=landmarks.schema,
        )

    def _restore_aligned_region(
        self,
        crop: np.ndarray,
        transformed_aligned: np.ndarray,
        transform: np.ndarray,
        region: Region,
    ) -> np.ndarray:
        inverse = cv2.invertAffineTransform(transform)
        local = inverse.copy()
        local[0, 2] -= float(region.x)
        local[1, 2] -= float(region.y)
        restored = cv2.warpAffine(
            transformed_aligned,
            local,
            (region.width, region.height),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
        )
        alpha = cv2.warpAffine(
            np.ones(transformed_aligned.shape[:2], dtype=np.float32),
            local,
            (region.width, region.height),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
        )
        alpha = np.clip(alpha, 0.0, 1.0)[..., None]
        return np.clip(
            crop.astype(np.float32) * (1.0 - alpha) + restored.astype(np.float32) * alpha,
            0,
            255,
        ).astype(np.uint8)

    def _effective_profile(self, requested: QualityProfile, frame_width: int) -> str:
        if frame_width <= 700:
            return QualityProfile.LOW_LATENCY.value
        if frame_width >= 1200:
            return QualityProfile.HIGH.value
        return requested.value

    def _context(self, stream_key: str, profile_name: str) -> StreamContext:
        now = time.perf_counter()
        with self._contexts_lock:
            context = self._contexts.get(stream_key)
            if context is None:
                context = StreamContext(profile_name=profile_name)
                context.configure(profile_name)
                self._contexts[stream_key] = context
            else:
                context.configure(profile_name)
            context.last_seen = now
            active = len(self._contexts)
        pipeline_health.update(active_processor_streams=active)
        return context

    def release_stream(self, stream_key: str) -> None:
        with self._contexts_lock:
            self._contexts.pop(stream_key, None)
            active = len(self._contexts)
        pipeline_health.update(active_processor_streams=active)

    def _prune_contexts(self, now: float) -> None:
        ttl = max(5, settings.processor_context_ttl_seconds)
        with self._contexts_lock:
            stale = [key for key, value in self._contexts.items() if now - value.last_seen > ttl]
            for key in stale:
                self._contexts.pop(key, None)
            active = len(self._contexts)
        if stale:
            pipeline_health.update(active_processor_streams=active)

    def _detect(self, rgb: np.ndarray, tracked: Region | None) -> Region | None:
        h, w = rgb.shape[:2]
        max_side = max(160, settings.detector_max_side)
        scale = min(1.0, max_side / float(max(h, w)))
        detector_input = rgb
        if scale < 1.0:
            detector_input = cv2.resize(
                rgb,
                (max(1, int(w * scale)), max(1, int(h * scale))),
                interpolation=cv2.INTER_AREA,
            )
        started = time.perf_counter()
        detections = self.detector.detect(detector_input)
        detector_window.add((time.perf_counter() - started) * 1000.0)
        if scale < 1.0:
            inverse = 1.0 / scale
            detections = [
                Region(
                    int(round(r.x * inverse)),
                    int(round(r.y * inverse)),
                    int(round(r.width * inverse)),
                    int(round(r.height * inverse)),
                    r.score,
                ).clamp(w, h)
                for r in detections
            ]
            detections = [r for r in detections if r is not None]
        if not detections:
            return None
        if tracked is None:
            return max(detections, key=lambda r: r.area)
        return max(detections, key=lambda r: (r.iou(tracked), r.score, r.area))

    def _match_color(self, source: np.ndarray, transformed: np.ndarray) -> np.ndarray:
        src = source.astype(np.float32)
        dst = transformed.astype(np.float32)
        src_mean = src.reshape(-1, 3).mean(axis=0)
        dst_mean = dst.reshape(-1, 3).mean(axis=0)
        src_std = src.reshape(-1, 3).std(axis=0) + 1.0
        dst_std = dst.reshape(-1, 3).std(axis=0) + 1.0
        matched = (dst - dst_mean) * (src_std / dst_std) + src_mean
        mixed = dst * 0.35 + matched * 0.65
        return np.clip(mixed, 0, 255).astype(np.uint8)

    def _resize_for_budget(self, crop: np.ndarray, max_side: int) -> tuple[np.ndarray, tuple[int, int]]:
        h, w = crop.shape[:2]
        if max(h, w) <= max_side:
            return crop, (w, h)
        scale = max_side / float(max(h, w))
        size = (max(2, int(round(w * scale))), max(2, int(round(h * scale))))
        return cv2.resize(crop, size, interpolation=cv2.INTER_AREA), (w, h)

    def _feather_mask(self, width: int, height: int) -> np.ndarray:
        feather = max(4, int(round(min(width, height) * 0.12)))
        yy, xx = np.mgrid[0:height, 0:width]
        edge_distance = np.minimum.reduce([xx, yy, width - 1 - xx, height - 1 - yy]).astype(np.float32)
        mask = np.clip(edge_distance / float(feather), 0.0, 1.0)
        sigma = max(1.0, feather / 3.0)
        return cv2.GaussianBlur(mask, (0, 0), sigmaX=sigma, sigmaY=sigma)[..., None]

    def _composite(self, rgb: np.ndarray, region: Region, transformed: np.ndarray) -> np.ndarray:
        started = time.perf_counter()
        try:
            output = rgb.copy()
            x0, y0 = region.x, region.y
            x1, y1 = x0 + region.width, y0 + region.height
            target = output[y0:y1, x0:x1]
            if transformed.shape[:2] != target.shape[:2]:
                transformed = cv2.resize(transformed, (region.width, region.height), interpolation=cv2.INTER_LINEAR)
            mask = self._feather_mask(region.width, region.height)
            blended = target.astype(np.float32) * (1.0 - mask) + transformed.astype(np.float32) * mask
            output[y0:y1, x0:x1] = np.clip(blended, 0, 255).astype(np.uint8)
            return output
        finally:
            compositor_window.add((time.perf_counter() - started) * 1000.0)

    def _cached_is_valid(self, context: StreamContext, now: float) -> bool:
        if context.cached_transform is None or context.tracker.region is None:
            return False
        if context.tracker.confidence < settings.tracker_min_confidence:
            return False
        ttl_ms = profile_defaults(context.profile_name).cache_ttl_ms
        return (now - context.cached_at) * 1000.0 <= ttl_ms

    def process_rgb(
        self,
        rgb: np.ndarray,
        effect: EffectProfile,
        stream_key: str,
        profile: QualityProfile,
        force_region: Region | None = None,
        force_inference: bool = False,
        crop_max_side_override: int | None = None,
        trace: ProcessingTrace | None = None,
    ) -> np.ndarray:
        h, w = rgb.shape[:2]
        profile_name = self._effective_profile(profile, w)
        context = self._context(stream_key, profile_name)
        context.frame_index += 1
        now = time.perf_counter()
        if context.frame_index % 120 == 0:
            self._prune_contexts(now)
        budget = context.budget
        assert budget is not None
        if crop_max_side_override is not None:
            budget.crop_max_side = max(64, min(1024, int(crop_max_side_override)))
        if trace is not None:
            trace.target_neural_fps = float(budget.target_neural_fps)
            trace.detector_interval = int(budget.detector_interval)
            trace.crop_max_side = int(budget.crop_max_side)
            trace.overload_events = int(budget.overload_events)

        tracked: Region | None = None
        if force_region is not None:
            forced = force_region.clamp(w, h)
            if forced is not None:
                try:
                    context.tracker.seed(rgb, forced)
                    tracked = forced
                except ValueError:
                    tracked = None
        elif context.tracker.region is not None:
            started = time.perf_counter()
            tracked = context.tracker.update(rgb)
            tracker_window.add((time.perf_counter() - started) * 1000.0)
            if tracked is None:
                tracking_reset_counter.increment()
                context.clear_cache()
                context.semantic_landmarks = None
            else:
                self._move_landmarks_with_tracker(context)

        frames_since_detection = context.frame_index - context.last_detection_frame
        detector_due = (
            force_region is None
            and (
                frames_since_detection >= budget.detector_interval
                or (
                    tracked is not None
                    and context.tracker.confidence < settings.tracker_min_confidence
                    and frames_since_detection >= 2
                )
            )
        )
        if detector_due:
            detected = self._detect(rgb, tracked)
            context.last_detection_frame = context.frame_index
            if detected is not None:
                try:
                    tracked = context.tracker.seed(rgb, detected)
                    self._refresh_landmarks(context, rgb, tracked)
                except ValueError:
                    tracked = None
                    context.semantic_landmarks = None
            elif tracked is None or context.tracker.confidence < settings.tracker_min_confidence:
                context.tracker.reset()
                context.clear_cache()
                context.semantic_landmarks = None
                pipeline_health.update(landmark_points=0)
                tracked = None

        region = tracked or context.tracker.region
        if region is None:
            pipeline_health.update(
                processed_region_width=0,
                processed_region_height=0,
                target_neural_fps=round(budget.target_neural_fps, 2),
                detector_interval=budget.detector_interval,
                crop_max_side=budget.crop_max_side,
                tracking_confidence=0.0,
                landmark_points=0,
                face_alignment_enabled=settings.face_alignment_enabled,
            )
            return rgb
        region = region.padded(settings.region_padding, w, h)
        if region is None:
            return rgb

        inference_due = budget.should_infer(now)
        if not force_inference and not inference_due:
            dropped_inference_counter.increment()
            if not self._cached_is_valid(context, now):
                return rgb
            cache_reuse_counter.increment()
            cached = context.cached_transform
            assert cached is not None
            pipeline_health.update(
                processed_region_width=region.width,
                processed_region_height=region.height,
                target_neural_fps=round(budget.target_neural_fps, 2),
                detector_interval=budget.detector_interval,
                crop_max_side=budget.crop_max_side,
                tracking_confidence=round(context.tracker.confidence, 3),
                landmark_points=0 if context.semantic_landmarks is None else context.semantic_landmarks.count,
                face_alignment_enabled=settings.face_alignment_enabled,
            )
            return self._composite(rgb, region, cached)

        x0, y0 = region.x, region.y
        x1, y1 = x0 + region.width, y0 + region.height
        crop = rgb[y0:y1, x0:x1]
        aligned = None
        if (
            settings.face_alignment_enabled
            and effect == EffectProfile.CUSTOM_ONNX
            and context.semantic_landmarks is not None
        ):
            aligned = align_face(rgb, context.semantic_landmarks, settings.face_alignment_size)
        source_for_model = aligned.image if aligned is not None else crop
        model_input, original_size = self._resize_for_budget(source_for_model, budget.crop_max_side)
        if trace is not None:
            trace.inference_attempted = True
            trace.model_input_height = int(model_input.shape[0])
            trace.model_input_width = int(model_input.shape[1])
        model_started = time.perf_counter()
        overload_before = budget.overload_events
        try:
            transformed = self.visual.process_rgb(model_input, effect)
            model_ms = (time.perf_counter() - model_started) * 1000.0
            model_window.add(model_ms)
            if trace is not None:
                trace.model_ms = model_ms
                trace.inference_succeeded = True
            budget.observe_inference(model_ms)
            if budget.overload_events > overload_before:
                overload_event_counter.increment(budget.overload_events - overload_before)
            if transformed.shape[:2] != (original_size[1], original_size[0]):
                transformed = cv2.resize(transformed, original_size, interpolation=cv2.INTER_LINEAR)
            if aligned is not None:
                transformed = self._restore_aligned_region(crop, transformed, aligned.transform, region)
            if effect == EffectProfile.CUSTOM_ONNX:
                transformed = self._match_color(crop, transformed)
            context.cached_transform = transformed
            context.cached_at = now
        except Exception:
            inference_error_counter.increment()
            if trace is not None:
                trace.model_ms = (time.perf_counter() - model_started) * 1000.0
                trace.inference_error = True
            if not self._cached_is_valid(context, now):
                return rgb
            transformed = context.cached_transform
            assert transformed is not None
            cache_reuse_counter.increment()

        if trace is not None:
            trace.target_neural_fps = float(budget.target_neural_fps)
            trace.detector_interval = int(budget.detector_interval)
            trace.crop_max_side = int(budget.crop_max_side)
            trace.overload_events = int(budget.overload_events)
        pipeline_health.update(
            processed_region_width=region.width,
            processed_region_height=region.height,
            target_neural_fps=round(budget.target_neural_fps, 2),
            detector_interval=budget.detector_interval,
            crop_max_side=budget.crop_max_side,
            tracking_confidence=round(context.tracker.confidence, 3),
            landmark_points=0 if context.semantic_landmarks is None else context.semantic_landmarks.count,
            face_alignment_enabled=settings.face_alignment_enabled,
        )
        return self._composite(rgb, region, transformed)


class TimedProcessor:
    def __init__(self, localized: LocalizedTemporalProcessor | None = None):
        self.visual = VisualProcessor()
        self.localized = localized or LocalizedTemporalProcessor(visual=self.visual)

    def _should_localize(self, effect: EffectProfile) -> bool:
        configured = {value.lower() for value in settings.localized_effects}
        return "all" in configured or effect.value.lower() in configured

    async def process(
        self,
        frame: VideoFrame,
        effect: EffectProfile,
        stream_key: str = "default",
        profile: QualityProfile = QualityProfile.BALANCED,
        force_region: Region | None = None,
        force_inference: bool = False,
        crop_max_side_override: int | None = None,
        trace: ProcessingTrace | None = None,
    ) -> VideoFrame:
        started = time.perf_counter()
        try:
            if effect == EffectProfile.ORIGINAL:
                return frame
            if not self._should_localize(effect) and force_region is None:
                return await self.visual.process(frame, effect)
            rgb = frame.to_ndarray(format="rgb24")
            out = self.localized.process_rgb(
                rgb,
                effect,
                stream_key,
                profile,
                force_region=force_region,
                force_inference=force_inference,
                crop_max_side_override=crop_max_side_override,
                trace=trace,
            )
            return _video_frame(out, frame)
        finally:
            total_ms = (time.perf_counter() - started) * 1000.0
            processor_window.add(total_ms)
            if trace is not None:
                trace.total_ms = total_ms

    def release_stream(self, stream_key: str) -> None:
        self.localized.release_stream(stream_key)

    def vision_status(self) -> dict:
        return self.localized.vision_status()


def _video_frame(rgb: np.ndarray, source: VideoFrame) -> VideoFrame:
    result = VideoFrame.from_ndarray(rgb, format="rgb24")
    result.pts = source.pts
    result.time_base = source.time_base
    return result


processor = TimedProcessor()
