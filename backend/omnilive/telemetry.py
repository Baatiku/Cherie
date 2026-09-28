from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from threading import Lock


@dataclass(frozen=True)
class Snapshot:
    count: int
    p50_ms: float
    p95_ms: float


class LatencyWindow:
    def __init__(self, size: int = 300):
        self._values = deque(maxlen=max(10, size))
        self._lock = Lock()

    def add(self, value_ms: float) -> None:
        if value_ms < 0:
            return
        with self._lock:
            self._values.append(float(value_ms))

    def snapshot(self) -> Snapshot:
        with self._lock:
            values = sorted(self._values)
        if not values:
            return Snapshot(0, 0.0, 0.0)

        def percentile(q: float) -> float:
            index = min(len(values) - 1, max(0, round((len(values) - 1) * q)))
            return values[index]

        return Snapshot(len(values), percentile(0.50), percentile(0.95))


class Counter:
    def __init__(self):
        self._value = 0
        self._lock = Lock()

    def increment(self, amount: int = 1) -> None:
        if amount <= 0:
            return
        with self._lock:
            self._value += int(amount)

    @property
    def value(self) -> int:
        with self._lock:
            return self._value


class PipelineHealth:
    def __init__(self):
        self._lock = Lock()
        self._state = {
            "detector_backend": "uninitialized",
            "tracker_backend": "uninitialized",
            "landmark_backend": "none",
            "landmark_points": 0,
            "face_alignment_enabled": False,
            "active_processor_streams": 0,
            "processed_region_width": 0,
            "processed_region_height": 0,
            "target_neural_fps": 0.0,
            "detector_interval": 0,
            "crop_max_side": 0,
            "tracking_confidence": 0.0,
        }

    def update(self, **values) -> None:
        with self._lock:
            self._state.update(values)

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._state)


media_wait_window = LatencyWindow()
input_age_window = LatencyWindow()
processor_window = LatencyWindow()
detector_window = LatencyWindow()
tracker_window = LatencyWindow()
landmark_window = LatencyWindow()
model_window = LatencyWindow()
compositor_window = LatencyWindow()

dropped_inference_counter = Counter()
input_overwrite_counter = Counter()
cache_reuse_counter = Counter()
tracking_reset_counter = Counter()
inference_error_counter = Counter()
overload_event_counter = Counter()
frame_correlation_match_counter = Counter()
frame_correlation_drop_counter = Counter()
frame_correlation_out_of_order_counter = Counter()
frame_timing_publish_drop_counter = Counter()

pipeline_health = PipelineHealth()
