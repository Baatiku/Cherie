import os
from dataclasses import dataclass


def _csv(name: str, default: str) -> tuple[str, ...]:
    return tuple(v.strip() for v in os.getenv(name, default).split(",") if v.strip())


def _bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


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
    turn_shared_secret: str = os.getenv("OMNILIVE_TURN_SHARED_SECRET", "")
    turn_credential_ttl_seconds: int = int(os.getenv("OMNILIVE_TURN_CREDENTIAL_TTL_SECONDS", "900"))
    turn_user: str = os.getenv("OMNILIVE_TURN_USER", "omnilive")
    detector_max_side: int = int(os.getenv("OMNILIVE_DETECTOR_MAX_SIDE", "640"))
    detector_backend: str = os.getenv("OMNILIVE_DETECTOR_BACKEND", "auto").strip().lower()
    face_detector_model: str = os.getenv(
        "OMNILIVE_FACE_DETECTOR_MODEL", "detectors/scrfd_2.5g.onnx"
    )
    face_detector_confidence: float = float(os.getenv("OMNILIVE_FACE_DETECTOR_CONFIDENCE", "0.50"))
    face_detector_nms: float = float(os.getenv("OMNILIVE_FACE_DETECTOR_NMS", "0.40"))
    landmark_backend: str = os.getenv("OMNILIVE_LANDMARK_BACKEND", "auto").strip().lower()
    face_landmark_model: str = os.getenv(
        "OMNILIVE_FACE_LANDMARK_MODEL", "landmarks/face_landmark_5.onnx"
    )
    face_alignment_enabled: bool = _bool("OMNILIVE_FACE_ALIGNMENT_ENABLED", False)
    face_alignment_size: int = int(os.getenv("OMNILIVE_FACE_ALIGNMENT_SIZE", "256"))
    person_detector_model: str = os.getenv(
        "OMNILIVE_PERSON_DETECTOR_MODEL", "detectors/person_yolo.onnx"
    )
    region_padding: float = float(os.getenv("OMNILIVE_REGION_PADDING", "0.22"))
    tracker_min_confidence: float = float(os.getenv("OMNILIVE_TRACKER_MIN_CONFIDENCE", "0.22"))
    processor_context_ttl_seconds: int = int(os.getenv("OMNILIVE_PROCESSOR_CONTEXT_TTL_SECONDS", "30"))
    localized_effects: tuple[str, ...] = _csv("OMNILIVE_LOCALIZED_EFFECTS", "custom_onnx")


settings = Settings()
