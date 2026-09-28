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
    def __init__(self, size:int=300):
        self._values=deque(maxlen=max(10,size))
        self._lock=Lock()

    def add(self, value_ms:float) -> None:
        if value_ms < 0:
            return
        with self._lock:
            self._values.append(float(value_ms))

    def snapshot(self) -> Snapshot:
        with self._lock:
            values=sorted(self._values)
        if not values:
            return Snapshot(0,0.0,0.0)
        def percentile(q:float) -> float:
            index=min(len(values)-1,max(0,round((len(values)-1)*q)))
            return values[index]
        return Snapshot(len(values),percentile(0.50),percentile(0.95))

latency_window=LatencyWindow()
