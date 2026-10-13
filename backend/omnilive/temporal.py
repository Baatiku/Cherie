from __future__ import annotations

from dataclasses import dataclass
import time


@dataclass(frozen=True)
class PipelineProfile:
    neural_fps: float
    detector_interval: int
    crop_max_side: int
    cache_ttl_ms: int


PROFILE_DEFAULTS = {
    "low_latency": PipelineProfile(25.0, 12, 256, 120),
    "balanced": PipelineProfile(30.0, 8, 384, 120),
    "high": PipelineProfile(30.0, 6, 448, 120),
}


def profile_defaults(name: str) -> PipelineProfile:
    return PROFILE_DEFAULTS.get(name, PROFILE_DEFAULTS["balanced"])


class AdaptiveInferenceBudget:
    """Fast load shedding with deliberately stable recovery to avoid oscillation."""

    def __init__(self, profile: PipelineProfile, min_neural_fps: float = 18.0):
        self.base = profile
        self.min_neural_fps = max(1.0, min(min_neural_fps, profile.neural_fps))
        self.target_neural_fps = profile.neural_fps
        self.detector_interval = profile.detector_interval
        self.crop_max_side = profile.crop_max_side
        self._last_inference_at = 0.0
        self._overload_streak = 0
        self._healthy_streak = 0
        self.overload_events = 0

    def should_infer(self, now: float | None = None) -> bool:
        current = time.perf_counter() if now is None else now
        interval = 1.0 / max(self.min_neural_fps, self.target_neural_fps)
        if self._last_inference_at <= 0.0 or current - self._last_inference_at >= interval:
            self._last_inference_at = current
            return True
        return False

    def observe_inference(self, latency_ms: float) -> None:
        frame_budget_ms = 1000.0 / max(self.min_neural_fps, self.target_neural_fps)
        if latency_ms > frame_budget_ms * 1.15:
            self._overload_streak += 1
            self._healthy_streak = 0
            if self._overload_streak >= 2:
                self.target_neural_fps = max(self.min_neural_fps, self.target_neural_fps * 0.85)
                self.detector_interval = min(self.base.detector_interval * 2, self.detector_interval + 1)
                self.crop_max_side = max(160, int(self.crop_max_side * 0.85))
                self.overload_events += 1
                self._overload_streak = 0
            return

        self._overload_streak = 0
        if latency_ms < frame_budget_ms * 0.75:
            self._healthy_streak += 1
            if self._healthy_streak >= 5:
                self.target_neural_fps = min(self.base.neural_fps, self.target_neural_fps + 1.5)
                self.detector_interval = max(self.base.detector_interval, self.detector_interval - 1)
                self.crop_max_side = min(self.base.crop_max_side, self.crop_max_side + 32)
                self._healthy_streak = 0
        else:
            self._healthy_streak = 0

