from omnilive.frame_policy import decide_frame
from omnilive.models import FrameMeta

def meta(ts):
    return FrameMeta(session_id="s"*16, sequence=0, captured_at_ms=ts, width=1280, height=720)

def test_fresh_frame_is_accepted():
    assert decide_frame(meta(990), now_ms=1000).accept is True

def test_stale_frame_is_dropped():
    result = decide_frame(meta(1), now_ms=1000)
    assert result.accept is False
    assert result.reason == "stale"
