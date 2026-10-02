"""Inspect the installed aiortc encoder defaults (bitrate knobs).

Run from backend/:  python -m modal run scripts/inspect_aiortc.py
"""

from __future__ import annotations

import modal

image = modal.Image.debian_slim(python_version="3.12").pip_install("aiortc>=1.9,<2")
app = modal.App("omnilive-aiortc-inspect")


@app.function(image=image, timeout=300)
def inspect() -> dict:
    import aiortc
    import inspect as py_inspect

    report: dict = {"aiortc_version": getattr(aiortc, "__version__", "unknown"), "hits": []}
    import aiortc.codecs.h264 as h264
    import aiortc.codecs.vpx as vpx
    import aiortc.rtcrtpsender as sender

    for module, label in ((h264, "h264"), (vpx, "vpx"), (sender, "rtpsender")):
        try:
            source = py_inspect.getsource(module)
        except Exception:
            continue
        for i, line in enumerate(source.splitlines()):
            low = line.lower()
            if "bit_rate" in low or "bitrate" in low or "max_br" in low or "rc_target" in low:
                report["hits"].append(f"{label}:{i + 1}:{line.strip()[:160]}")
    print(report)
    return report
