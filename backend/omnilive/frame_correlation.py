from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass

from .telemetry import (
    frame_correlation_drop_counter,
    frame_correlation_match_counter,
    frame_correlation_out_of_order_counter,
)

_RTP_MASK = 0xFFFFFFFF
_RTP_WRAP = 0x100000000


def _normalize_rtp(value: int) -> int:
    return int(value) & _RTP_MASK


def _rtp_distance(a: int, b: int) -> int:
    delta = (_normalize_rtp(a) - _normalize_rtp(b)) & _RTP_MASK
    if delta > 0x7FFFFFFF:
        delta -= _RTP_WRAP
    return abs(delta)


@dataclass
class _CaptureMark:
    sequence: int
    capture_timestamp_ns: int
    capture_elapsed_ns: int
    rtp_guess: int
    received_ns: int


@dataclass
class _FrameState:
    rtp_timestamp: int
    received_ns: int
    sequence: int | None = None
    processor_started_ns: int | None = None
    processor_ended_ns: int | None = None


class FrameCorrelator:
    """Join Android capture marks to decoded RTP frames without delaying video."""

    def __init__(
        self,
        max_pending: int = 128,
        calibration_window_ms: int = 250,
        match_tolerance_ticks: int = 4,
    ):
        self.max_pending = max(16, int(max_pending))
        self.calibration_window_ns = max(20, int(calibration_window_ms)) * 1_000_000
        self.match_tolerance_ticks = max(0, int(match_tolerance_ticks))
        self._marks: OrderedDict[int, _CaptureMark] = OrderedDict()
        self._frames: OrderedDict[int, _FrameState] = OrderedDict()
        self._rtp_offset: int | None = None
        self._last_sequence = -1

    @property
    def calibrated(self) -> bool:
        return self._rtp_offset is not None

    @property
    def rtp_offset(self) -> int | None:
        return self._rtp_offset

    def note_capture(
        self,
        sequence: int,
        capture_timestamp_ns: int,
        capture_elapsed_ns: int,
        rtp_guess: int,
        received_ns: int,
    ) -> list[dict]:
        sequence = int(sequence)
        if sequence <= self._last_sequence:
            frame_correlation_out_of_order_counter.increment()
            return []
        self._last_sequence = sequence
        self._marks[sequence] = _CaptureMark(
            sequence=sequence,
            capture_timestamp_ns=max(0, int(capture_timestamp_ns)),
            capture_elapsed_ns=max(0, int(capture_elapsed_ns)),
            rtp_guess=_normalize_rtp(rtp_guess),
            received_ns=max(0, int(received_ns)),
        )
        self._match()
        self._evict()
        return self._collect_ready()

    def note_frame_received(self, rtp_timestamp: int, received_ns: int) -> list[dict]:
        rtp = _normalize_rtp(rtp_timestamp)
        self._frames[rtp] = _FrameState(rtp_timestamp=rtp, received_ns=max(0, int(received_ns)))
        self._match()
        self._evict()
        return self._collect_ready()

    def note_processed(
        self,
        rtp_timestamp: int,
        processor_started_ns: int,
        processor_ended_ns: int,
    ) -> list[dict]:
        rtp = _normalize_rtp(rtp_timestamp)
        frame = self._frames.get(rtp)
        if frame is None:
            frame_correlation_drop_counter.increment()
            return []
        frame.processor_started_ns = max(frame.received_ns, int(processor_started_ns))
        frame.processor_ended_ns = max(frame.processor_started_ns, int(processor_ended_ns))
        self._match()
        return self._collect_ready()

    def _match(self) -> None:
        if not self._marks or not self._frames:
            return
        if self._rtp_offset is None:
            self._calibrate()
        if self._rtp_offset is None:
            return

        assigned_sequences = {frame.sequence for frame in self._frames.values() if frame.sequence is not None}
        available_marks = [mark for mark in self._marks.values() if mark.sequence not in assigned_sequences]
        if not available_marks:
            return

        for frame in self._frames.values():
            if frame.sequence is not None:
                continue
            best = min(
                available_marks,
                key=lambda mark: _rtp_distance(
                    frame.rtp_timestamp,
                    mark.rtp_guess + self._rtp_offset,
                ),
            )
            distance = _rtp_distance(frame.rtp_timestamp, best.rtp_guess + self._rtp_offset)
            if distance <= self.match_tolerance_ticks:
                frame.sequence = best.sequence
                available_marks.remove(best)
                if not available_marks:
                    break

    def _calibrate(self) -> None:
        candidates: list[tuple[int, _CaptureMark, _FrameState]] = []
        for mark in self._marks.values():
            for frame in self._frames.values():
                if frame.sequence is not None:
                    continue
                arrival_delta = abs(mark.received_ns - frame.received_ns)
                if arrival_delta <= self.calibration_window_ns:
                    candidates.append((arrival_delta, mark, frame))
        if not candidates:
            return
        _, mark, frame = min(candidates, key=lambda item: item[0])
        self._rtp_offset = _normalize_rtp(frame.rtp_timestamp - mark.rtp_guess)
        frame.sequence = mark.sequence

    def _collect_ready(self) -> list[dict]:
        ready: list[dict] = []
        for rtp, frame in list(self._frames.items()):
            if frame.sequence is None or frame.processor_ended_ns is None or frame.processor_started_ns is None:
                continue
            mark = self._marks.pop(frame.sequence, None)
            if mark is None:
                self._frames.pop(rtp, None)
                frame_correlation_drop_counter.increment()
                continue
            ready.append(
                {
                    "sequence": mark.sequence,
                    "capture_timestamp_ns": mark.capture_timestamp_ns,
                    "capture_elapsed_ns": mark.capture_elapsed_ns,
                    "rtp_timestamp": frame.rtp_timestamp,
                    "backend_receive_ns": frame.received_ns,
                    "processor_start_ns": frame.processor_started_ns,
                    "processor_end_ns": frame.processor_ended_ns,
                    "backend_wait_ms": round((frame.processor_started_ns - frame.received_ns) / 1_000_000.0, 3),
                    "backend_processor_ms": round(
                        (frame.processor_ended_ns - frame.processor_started_ns) / 1_000_000.0, 3
                    ),
                    "backend_total_ms": round((frame.processor_ended_ns - frame.received_ns) / 1_000_000.0, 3),
                }
            )
            self._frames.pop(rtp, None)
            frame_correlation_match_counter.increment()
        return ready

    def _evict(self) -> None:
        while len(self._marks) > self.max_pending:
            self._marks.popitem(last=False)
            frame_correlation_drop_counter.increment()
        while len(self._frames) > self.max_pending:
            self._frames.popitem(last=False)
            frame_correlation_drop_counter.increment()
