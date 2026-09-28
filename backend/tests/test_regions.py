import numpy as np
from omnilive.regions import LandmarkRegionTracker, Region


def test_region_padding_clamps_to_frame():
    region = Region(2, 3, 20, 10).padded(0.5, 24, 16)
    assert region is not None
    assert region.x == 0
    assert region.y == 0
    assert region.x + region.width <= 24
    assert region.y + region.height <= 16


def test_region_iou_is_stable():
    a = Region(0, 0, 10, 10)
    b = Region(5, 0, 10, 10)
    assert round(a.iou(b), 3) == 0.333


def test_sparse_tracker_follows_translation():
    first = np.zeros((80, 100, 3), dtype=np.uint8)
    for y in range(20, 50, 8):
        for x in range(25, 55, 8):
            first[y:y+3, x:x+3] = 255
    second = np.roll(first, shift=(3, 4), axis=(0, 1))
    tracker = LandmarkRegionTracker(min_points=3)
    tracker.seed(first, Region(20, 15, 45, 45))
    updated = tracker.update(second)
    assert updated is not None
    assert updated.x >= 22
    assert updated.y >= 17
    assert tracker.confidence > 0
