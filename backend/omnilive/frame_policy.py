import time
from .config import settings
from .models import FrameDecision, FrameMeta

def decide_frame(meta: FrameMeta, now_ms: int | None = None) -> FrameDecision:
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    age = max(0, now - meta.captured_at_ms)
    if age > settings.max_frame_age_ms:
        return FrameDecision(accept=False, age_ms=age, reason="stale")
    return FrameDecision(accept=True, age_ms=age, reason="fresh")
