from omnilive.frame_correlation import FrameCorrelator


def test_frame_correlator_calibrates_and_reports_backend_timing():
    correlator = FrameCorrelator(max_pending=16)
    correlator.note_frame_received(900_100, 1_000_000_000)
    assert correlator.note_capture(1, 50_000_000, 60_000_000, 100, 1_005_000_000) == []
    reports = correlator.note_processed(900_100, 1_010_000_000, 1_025_000_000)

    assert correlator.calibrated
    assert correlator.rtp_offset == 900_000
    assert len(reports) == 1
    assert reports[0]["sequence"] == 1
    assert reports[0]["rtp_timestamp"] == 900_100
    assert reports[0]["backend_wait_ms"] == 10.0
    assert reports[0]["backend_processor_ms"] == 15.0
    assert reports[0]["backend_total_ms"] == 25.0


def test_frame_correlator_skips_capture_mark_for_dropped_video_frame():
    correlator = FrameCorrelator(max_pending=16)
    correlator.note_capture(1, 10, 10, 100, 1_000_000_000)
    correlator.note_frame_received(500_100, 1_002_000_000)
    first = correlator.note_processed(500_100, 1_003_000_000, 1_004_000_000)
    assert first[0]["sequence"] == 1

    correlator.note_capture(2, 20, 20, 3_100, 1_030_000_000)
    correlator.note_capture(3, 30, 30, 6_100, 1_060_000_000)
    correlator.note_frame_received(506_100, 1_061_000_000)
    reports = correlator.note_processed(506_100, 1_062_000_000, 1_063_000_000)

    assert len(reports) == 1
    assert reports[0]["sequence"] == 3
