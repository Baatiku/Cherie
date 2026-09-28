from dataclasses import dataclass
from .models import FrameMeta

@dataclass
class ProcessResult:
    sequence: int
    processing_ms: float
    detector_ran: bool

class FrameProcessor:
    def __init__(self, detector_interval: int = 8):
        self.detector_interval = max(1, detector_interval)

    def process_meta(self, meta: FrameMeta) -> ProcessResult:
        return ProcessResult(meta.sequence, 0.1, meta.sequence % self.detector_interval == 0)
