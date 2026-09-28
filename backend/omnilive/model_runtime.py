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

    @property
    def adapter(self) -> str:
        return (getattr(settings, "model_adapter", "single_image") or "single_image").strip().lower()

    @property
    def requires_reference(self) -> bool:
        return self.adapter == "dual_image"

    def input_status(self) -> dict:
        session = self._session
        if session is None:
            return {
                "adapter": self.adapter,
                "reference_required": self.requires_reference,
                "input_name": None,
                "input_shape": None,
                "input_type": None,
                "inputs": [],
            }
        inputs = [
            {
                "name": meta.name,
                "shape": list(meta.shape),
                "type": getattr(meta, "type", None),
            }
            for meta in session.get_inputs()
        ]
        first = inputs[0] if inputs else {"name": None, "shape": None, "type": None}
        return {
            "adapter": self.adapter,
            "reference_required": self.requires_reference,
            "input_name": first["name"],
            "input_shape": first["shape"],
            "input_type": first["type"],
            "inputs": inputs,
        }

    def _select_input_roles(self, session):
        inputs = list(session.get_inputs())
        if not inputs:
            raise RuntimeError("custom model has no inputs")
        if self.adapter == "single_image":
            return inputs[0], None
        if self.adapter != "dual_image":
            raise RuntimeError("unsupported OMNILIVE_MODEL_ADAPTER: " + self.adapter)
        if len(inputs) != 2:
            raise RuntimeError("dual_image adapter requires exactly two ONNX inputs")

        by_name = {meta.name: meta for meta in inputs}
        live_name = getattr(settings, "model_live_input", "") or ""
        reference_name = getattr(settings, "model_reference_input", "") or ""
        live_meta = by_name.get(live_name) if live_name else inputs[0]
        if live_meta is None:
            raise RuntimeError("configured live model input not found: " + live_name)

        if reference_name:
            reference_meta = by_name.get(reference_name)
            if reference_meta is None:
                raise RuntimeError("configured reference model input not found: " + reference_name)
        else:
            reference_meta = next((meta for meta in inputs if meta.name != live_meta.name), None)
        if reference_meta is None or reference_meta.name == live_meta.name:
            raise RuntimeError("dual_image adapter could not resolve distinct live/reference inputs")
        return live_meta, reference_meta

    @staticmethod
    def _prepare_image(rgb: np.ndarray, meta) -> tuple[np.ndarray, bool]:
        shape = list(meta.shape)
        if len(shape) != 4:
            raise RuntimeError("custom model image inputs must be 4D NCHW or NHWC")
        h, w = rgb.shape[:2]
        import cv2

        nchw = shape[1] == 3 or shape[1] == "3"
        if nchw:
            target_h = shape[2] if isinstance(shape[2], int) else h
            target_w = shape[3] if isinstance(shape[3], int) else w
        else:
            target_h = shape[1] if isinstance(shape[1], int) else h
            target_w = shape[2] if isinstance(shape[2], int) else w
        target_h = max(1, int(target_h))
        target_w = max(1, int(target_w))
        value = cv2.resize(rgb, (target_w, target_h)).astype(np.float32) / 255.0
        if nchw:
            value = np.transpose(value, (2, 0, 1))[None, ...]
        else:
            value = value[None, ...]
        return value, nchw

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

    def run_image(self, rgb: np.ndarray, reference_rgb: np.ndarray | None = None) -> np.ndarray:
        session = self.load(strict=True)
        if session is None:
            return rgb
        live_meta, reference_meta = self._select_input_roles(session)
        live_value, live_nchw = self._prepare_image(rgb, live_meta)
        feeds = {live_meta.name: live_value}

        if reference_meta is not None:
            if reference_rgb is None:
                raise RuntimeError("dual_image model requires a session reference image")
            reference_value, _ = self._prepare_image(reference_rgb, reference_meta)
            feeds[reference_meta.name] = reference_value

        h, w = rgb.shape[:2]
        import cv2

        y = session.run(None, feeds)[0]
        if y.ndim == 4:
            y = y[0]
        if live_nchw and y.ndim == 3 and y.shape[0] in (1, 3, 4):
            y = np.transpose(y, (1, 2, 0))
        y = np.clip(y, 0.0, 1.0)
        if y.ndim == 2:
            y = y[..., None]
        if y.shape[-1] == 1:
            y = np.repeat(y, 3, axis=-1)
        y = (y[..., :3] * 255.0).astype(np.uint8)
        return cv2.resize(y, (w, h), interpolation=cv2.INTER_LINEAR)


runtime = OnnxRuntime()
