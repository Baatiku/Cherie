from __future__ import annotations

from pathlib import Path
from threading import Lock
import os
import subprocess
import time

import numpy as np

from .config import settings


class OnnxRuntime:
    def __init__(self):
        self._lock = Lock()
        self._session = None
        self._model_path = None
        self._providers: list[str] = []
        self._last_error: str | None = None
        self._load_ms: float | None = None
        self._load_count = 0

    @property
    def loaded(self) -> bool:
        return self._session is not None

    @property
    def providers(self) -> list[str]:
        return list(self._providers)

    @property
    def model_path(self) -> str | None:
        return str(self._model_path) if self._model_path else None

    @property
    def last_error(self) -> str | None:
        return self._last_error

    @property
    def load_ms(self) -> float | None:
        return self._load_ms

    def _resolve_path(self) -> Path | None:
        model_id = (settings.model_id or "").strip()
        if not model_id or model_id == "none":
            return None
        root = Path(settings.model_root).resolve()
        candidate = (root / model_id).resolve()
        if root not in candidate.parents and candidate != root:
            raise ValueError("model path escapes configured model root")
        if candidate.is_dir():
            matches = sorted(candidate.glob("*.onnx"))
            if len(matches) != 1:
                raise RuntimeError("model directory must contain exactly one .onnx file")
            candidate = matches[0]
        if candidate.suffix.lower() != ".onnx":
            candidate = candidate.with_suffix(".onnx")
        if not candidate.exists():
            raise FileNotFoundError("configured model not found: " + str(candidate))
        return candidate

    def load(self, strict: bool = False):
        started = time.perf_counter()
        try:
            path = self._resolve_path()
            if path is None:
                return None
            with self._lock:
                if self._session is not None and self._model_path == path:
                    return self._session
                import onnxruntime as ort

                available = ort.get_available_providers()
                preferred = [
                    provider
                    for provider in (
                        "TensorrtExecutionProvider",
                        "CUDAExecutionProvider",
                        "CPUExecutionProvider",
                    )
                    if provider in available
                ]
                if not preferred:
                    preferred = ["CPUExecutionProvider"]
                self._session = ort.InferenceSession(str(path), providers=preferred)
                self._model_path = path
                self._providers = list(self._session.get_providers())
                self._last_error = None
                self._load_ms = (time.perf_counter() - started) * 1000.0
                self._load_count += 1
                return self._session
        except Exception as exc:
            self._last_error = str(exc)
            if strict:
                raise
            return None

    def input_status(self) -> dict:
        session = self._session
        if session is None:
            return {"input_name": None, "input_shape": None, "input_type": None}
        meta = session.get_inputs()[0]
        return {
            "input_name": meta.name,
            "input_shape": list(meta.shape),
            "input_type": getattr(meta, "type", None),
        }

    def memory_status(self) -> dict:
        rss_mb = None
        system_available_mb = None
        gpu_used_mb = None
        gpu_total_mb = None
        gpu_name = None

        try:
            with open("/proc/self/status", "r", encoding="utf-8") as handle:
                for line in handle:
                    if line.startswith("VmRSS:"):
                        rss_mb = round(int(line.split()[1]) / 1024.0, 2)
                        break
        except Exception:
            pass

        try:
            with open("/proc/meminfo", "r", encoding="utf-8") as handle:
                for line in handle:
                    if line.startswith("MemAvailable:"):
                        system_available_mb = round(int(line.split()[1]) / 1024.0, 2)
                        break
        except Exception:
            pass

        try:
            result = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=name,memory.used,memory.total",
                    "--format=csv,noheader,nounits",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=1.0,
            )
            first = result.stdout.strip().splitlines()[0] if result.returncode == 0 and result.stdout.strip() else ""
            if first:
                parts = [part.strip() for part in first.split(",")]
                if len(parts) >= 3:
                    gpu_name = parts[0]
                    gpu_used_mb = float(parts[1])
                    gpu_total_mb = float(parts[2])
        except Exception:
            pass

        return {
            "process_rss_mb": rss_mb,
            "system_available_mb": system_available_mb,
            "gpu_name": gpu_name,
            "gpu_used_mb": gpu_used_mb,
            "gpu_total_mb": gpu_total_mb,
        }

    def status(self) -> dict:
        configured = (settings.model_id or "none") != "none"
        return {
            "configured": configured,
            "model_id": settings.model_id,
            "loaded": self.loaded,
            "model_path": self.model_path,
            "providers": self.providers,
            "load_ms": self.load_ms,
            "load_count": self._load_count,
            "last_error": self.last_error,
            **self.input_status(),
        }

    def run_image(self, rgb: np.ndarray) -> np.ndarray:
        session = self.load(strict=True)
        if session is None:
            return rgb
        input_meta = session.get_inputs()[0]
        shape = input_meta.shape
        if len(shape) != 4:
            raise RuntimeError("custom model input must be 4D NCHW or NHWC")
        h, w = rgb.shape[:2]
        import cv2

        if shape[1] == 3 or shape[1] == "3":
            target_h = shape[2] if isinstance(shape[2], int) else h
            target_w = shape[3] if isinstance(shape[3], int) else w
            x = cv2.resize(rgb, (target_w, target_h)).astype(np.float32) / 255.0
            x = np.transpose(x, (2, 0, 1))[None, ...]
            nchw = True
        else:
            target_h = shape[1] if isinstance(shape[1], int) else h
            target_w = shape[2] if isinstance(shape[2], int) else w
            x = cv2.resize(rgb, (target_w, target_h)).astype(np.float32) / 255.0
            x = x[None, ...]
            nchw = False

        y = session.run(None, {input_meta.name: x})[0]
        if y.ndim == 4:
            y = y[0]
        if nchw and y.ndim == 3 and y.shape[0] in (1, 3, 4):
            y = np.transpose(y, (1, 2, 0))
        y = np.clip(y, 0.0, 1.0)
        if y.ndim == 2:
            y = y[..., None]
        if y.shape[-1] == 1:
            y = np.repeat(y, 3, axis=-1)
        y = (y[..., :3] * 255.0).astype(np.uint8)
        return cv2.resize(y, (w, h), interpolation=cv2.INTER_LINEAR)


runtime = OnnxRuntime()
