from omnilive.telemetry import LatencyWindow

def test_latency_window_percentiles():
    window=LatencyWindow(size=20)
    for value in [10,20,30,40,50,60,70,80,90,100]:
        window.add(value)
    snap=window.snapshot()
    assert snap.count==10
    assert snap.p50_ms in {50.0,60.0}
    assert snap.p95_ms>=90.0
