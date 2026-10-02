from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import cv2
import numpy as np


@dataclass(frozen=True)
class Region:
    x: int
    y: int
    width: int
    height: int
    score: float = 1.0
    # Optional 5-point facial keypoints in full-frame pixel coordinates,
    # ArcFace order: left eye, right eye, nose, mouth-left, mouth-right.
    kps: tuple[tuple[float, float], ...] | None = None

    @property
    def area(self) -> int:
        return max(0, self.width) * max(0, self.height)

    def clamp(self, frame_width: int, frame_height: int) -> "Region | None":
        x0 = max(0, min(frame_width, int(self.x)))
        y0 = max(0, min(frame_height, int(self.y)))
        x1 = max(x0, min(frame_width, int(self.x + self.width)))
        y1 = max(y0, min(frame_height, int(self.y + self.height)))
        if x1 - x0 < 2 or y1 - y0 < 2:
            return None
        shifted = None
        if self.kps is not None:
            dx = float(x0 - int(self.x))
            dy = float(y0 - int(self.y))
            shifted = tuple((px + dx, py + dy) for px, py in self.kps)
        return Region(x0, y0, x1 - x0, y1 - y0, self.score, shifted)

    def padded(self, fraction: float, frame_width: int, frame_height: int) -> "Region | None":
        pad_x = int(round(self.width * max(0.0, fraction)))
        pad_y = int(round(self.height * max(0.0, fraction)))
        return Region(
            self.x - pad_x,
            self.y - pad_y,
            self.width + 2 * pad_x,
            self.height + 2 * pad_y,
            self.score,
        ).clamp(frame_width, frame_height)

    def iou(self, other: "Region") -> float:
        left = max(self.x, other.x)
        top = max(self.y, other.y)
        right = min(self.x + self.width, other.x + other.width)
        bottom = min(self.y + self.height, other.y + other.height)
        intersection = max(0, right - left) * max(0, bottom - top)
        union = self.area + other.area - intersection
        return float(intersection) / float(union) if union > 0 else 0.0


class RegionDetector(Protocol):
    name: str

    def detect(self, rgb: np.ndarray) -> list[Region]: ...


class OpenCvHaarFaceDetector:
    """Small zero-download detector used until a GPU detector adapter is configured."""

    name = "opencv_haar_face"

    def __init__(self, max_regions: int = 1, min_face_ratio: float = 0.08):
        self.max_regions = max(1, max_regions)
        self.min_face_ratio = max(0.03, min(0.5, min_face_ratio))
        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        self._cascade = cv2.CascadeClassifier(cascade_path)
        self._available = not self._cascade.empty()

    @property
    def available(self) -> bool:
        return self._available

    def detect(self, rgb: np.ndarray) -> list[Region]:
        if not self._available or rgb.size == 0:
            return []
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        h, w = gray.shape[:2]
        min_side = max(24, int(min(h, w) * self.min_face_ratio))
        detections = self._cascade.detectMultiScale(
            gray,
            scaleFactor=1.12,
            minNeighbors=4,
            minSize=(min_side, min_side),
            flags=cv2.CASCADE_SCALE_IMAGE,
        )
        regions = [Region(int(x), int(y), int(rw), int(rh), 1.0) for x, y, rw, rh in detections]
        regions.sort(key=lambda region: region.area, reverse=True)
        return regions[: self.max_regions]


class LandmarkRegionTracker:
    """Tracks sparse visual landmarks and carries a region between detector passes."""

    name = "lk_sparse_landmarks"

    def __init__(self, min_points: int = 5, max_points: int = 40):
        self.min_points = max(3, min_points)
        self.max_points = max(self.min_points, max_points)
        self.region: Region | None = None
        self.confidence = 0.0
        self.landmarks: np.ndarray | None = None
        self.last_transform: np.ndarray | None = None
        self._previous_gray: np.ndarray | None = None

    def reset(self) -> None:
        self.region = None
        self.confidence = 0.0
        self.landmarks = None
        self.last_transform = None
        self._previous_gray = None

    def _seed_points(self, gray: np.ndarray, region: Region) -> np.ndarray | None:
        mask = np.zeros_like(gray, dtype=np.uint8)
        x0, y0 = region.x, region.y
        x1, y1 = x0 + region.width, y0 + region.height
        mask[y0:y1, x0:x1] = 255
        points = cv2.goodFeaturesToTrack(
            gray,
            maxCorners=self.max_points,
            qualityLevel=0.01,
            minDistance=5,
            mask=mask,
            blockSize=5,
        )
        return points.astype(np.float32) if points is not None else None

    def seed(self, rgb: np.ndarray, region: Region) -> Region:
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        clamped = region.clamp(gray.shape[1], gray.shape[0])
        if clamped is None:
            self.reset()
            raise ValueError("cannot seed tracker with empty region")
        self.region = clamped
        self._previous_gray = gray
        self.landmarks = self._seed_points(gray, clamped)
        self.last_transform = None
        count = 0 if self.landmarks is None else len(self.landmarks)
        self.confidence = min(1.0, count / float(max(self.min_points, 12)))
        return clamped

    def update(self, rgb: np.ndarray) -> Region | None:
        if self.region is None or self._previous_gray is None or self.landmarks is None:
            return None
        if len(self.landmarks) < self.min_points:
            self.reset()
            return None

        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        next_points, status, _ = cv2.calcOpticalFlowPyrLK(
            self._previous_gray,
            gray,
            self.landmarks,
            None,
            winSize=(21, 21),
            maxLevel=3,
            criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 20, 0.03),
        )
        if next_points is None or status is None:
            self.reset()
            return None

        good_old = self.landmarks[status.reshape(-1) == 1].reshape(-1, 2)
        good_new = next_points[status.reshape(-1) == 1].reshape(-1, 2)
        if len(good_new) < self.min_points:
            self.reset()
            return None

        transform, inliers = cv2.estimateAffinePartial2D(
            good_old,
            good_new,
            method=cv2.RANSAC,
            ransacReprojThreshold=3.0,
            maxIters=50,
            confidence=0.95,
            refineIters=5,
        )
        if transform is None:
            self.reset()
            return None

        r = self.region
        corners = np.array(
            [[r.x, r.y], [r.x + r.width, r.y], [r.x + r.width, r.y + r.height], [r.x, r.y + r.height]],
            dtype=np.float32,
        ).reshape(-1, 1, 2)
        moved = cv2.transform(corners, transform).reshape(-1, 2)
        x0, y0 = np.floor(moved.min(axis=0)).astype(int)
        x1, y1 = np.ceil(moved.max(axis=0)).astype(int)
        updated = Region(int(x0), int(y0), int(x1 - x0), int(y1 - y0), r.score).clamp(gray.shape[1], gray.shape[0])
        if updated is None:
            self.reset()
            return None

        kept = good_new.reshape(-1, 1, 2).astype(np.float32)
        self.region = updated
        self.landmarks = kept
        self.last_transform = transform.astype(np.float32)
        self._previous_gray = gray
        inlier_ratio = float(np.mean(inliers)) if inliers is not None and len(inliers) else 1.0
        point_ratio = min(1.0, len(kept) / float(max(self.min_points, 12)))
        self.confidence = max(0.0, min(1.0, inlier_ratio * point_ratio))

        if len(kept) < max(self.min_points * 2, 12):
            refreshed = self._seed_points(gray, updated)
            if refreshed is not None and len(refreshed) >= len(kept):
                self.landmarks = refreshed
        return updated
