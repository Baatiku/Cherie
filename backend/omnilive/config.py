import os
from dataclasses import dataclass


def _csv(name: str, default: str) -> tuple[str, ...]:
    return tuple(v.strip() for v in os.getenv(name, default).split(",") if v.strip())


@dataclass(frozen=True)
class Settings:
    session_secret: str = os.getenv("OMNILIVE_SESSION_SECRET", "development-only-secret")
    session_ttl_seconds: int = int(os.getenv("OMNILIVE_SESSION_TTL_SECONDS", "900"))
    max_frame_age_ms: int = int(os.getenv("OMNILIVE_MAX_FRAME_AGE_MS", "140"))
    target_fps: int = int(os.getenv("OMNILIVE_TARGET_FPS", "30"))
    model_id: str = os.getenv("OMNILIVE_MODEL_ID", "none")
    model_root: str = os.getenv("OMNILIVE_MODEL_ROOT", "/models")
    ice_urls: tuple[str, ...] = _csv("OMNILIVE_ICE_URLS", "stun:stun.l.google.com:19302")
    ice_username: str = os.getenv("OMNILIVE_ICE_USERNAME", "")
    ice_credential: str = os.getenv("OMNILIVE_ICE_CREDENTIAL", "")
    detector_max_side: int = int(os.getenv("OMNILIVE_DETECTOR_MAX_SIDE", "640"))
    region_padding: float = float(os.getenv("OMNILIVE_REGION_PADDING", "0.22"))
    tracker_min_confidence: float = float(os.getenv("OMNILIVE_TRACKER_MIN_CONFIDENCE", "0.22"))
    processor_context_ttl_seconds: int = int(os.getenv("OMNILIVE_PROCESSOR_CONTEXT_TTL_SECONDS", "30"))
    localized_effects: tuple[str, ...] = _csv("OMNILIVE_LOCALIZED_EFFECTS", "custom_onnx")


settings = Settings()
