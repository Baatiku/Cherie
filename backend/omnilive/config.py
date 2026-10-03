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
    max_sessions: int = int(os.getenv("OMNILIVE_MAX_SESSIONS", "32"))
    live_handshake_timeout_seconds: float = float(os.getenv("OMNILIVE_LIVE_HANDSHAKE_TIMEOUT_SECONDS", "15"))
    live_max_duration_seconds: float = float(os.getenv("OMNILIVE_LIVE_MAX_DURATION_SECONDS", "3600"))
    live_max_idle_seconds: float = float(os.getenv("OMNILIVE_LIVE_MAX_IDLE_SECONDS", "60"))
    enable_benchmark: bool = _bool("OMNILIVE_ENABLE_BENCHMARK", True)
    cors_allow_origins: tuple[str, ...] = _csv("OMNILIVE_CORS_ALLOW_ORIGINS", "")
    is_production: bool = _bool("OMNILIVE_PRODUCTION", False)
    max_frame_age_ms: int = int(os.getenv("OMNILIVE_MAX_FRAME_AGE_MS", "140"))
    target_fps: int = int(os.getenv("OMNILIVE_TARGET_FPS", "30"))
    model_id: str = os.getenv("OMNILIVE_MODEL_ID", "none")
    model_root: str = os.getenv("OMNILIVE_MODEL_ROOT", "/models")
    model_adapter: str = os.getenv("OMNILIVE_MODEL_ADAPTER", "single_image").strip().lower()
    model_live_input: str = os.getenv("OMNILIVE_MODEL_LIVE_INPUT", "").strip()
    model_reference_input: str = os.getenv("OMNILIVE_MODEL_REFERENCE_INPUT", "").strip()
    model_allow_cpu_fallback: bool = _bool("OMNILIVE_MODEL_ALLOW_CPU_FALLBACK", False)
    model_retry_cooldown_seconds: float = float(os.getenv("OMNILIVE_MODEL_RETRY_COOLDOWN_SECONDS", "1.5"))
    model_provider_quarantine_seconds: float = float(os.getenv("OMNILIVE_MODEL_PROVIDER_QUARANTINE_SECONDS", "30"))
    ice_urls: tuple[str, ...] = _csv(
        "OMNILIVE_ICE_URLS",
        "stun:stun.l.google.com:19302,turn:openrelay.metered.ca:80,turn:openrelay.metered.ca:443,turn:openrelay.metered.ca:443?transport=tcp",
    )
    ice_username: str = os.getenv("OMNILIVE_ICE_USERNAME", "openrelayproject")
    ice_credential: str = os.getenv("OMNILIVE_ICE_CREDENTIAL", "openrelayproject")
    turn_shared_secret: str = os.getenv("OMNILIVE_TURN_SHARED_SECRET", "")
    turn_credential_ttl_seconds: int = int(os.getenv("OMNILIVE_TURN_CREDENTIAL_TTL_SECONDS", "900"))
    turn_user: str = os.getenv("OMNILIVE_TURN_USER", "omnilive")
    detector_max_side: int = int(os.getenv("OMNILIVE_DETECTOR_MAX_SIDE", "640"))
    detector_backend: str = os.getenv("OMNILIVE_DETECTOR_BACKEND", "auto").strip().lower()
    face_detector_model: str = os.getenv(
        "OMNILIVE_FACE_DETECTOR_MODEL", "detectors/det_10g.onnx"
    )
    face_detector_confidence: float = float(os.getenv("OMNILIVE_FACE_DETECTOR_CONFIDENCE", "0.50"))
    face_detector_nms: float = float(os.getenv("OMNILIVE_FACE_DETECTOR_NMS", "0.40"))
    landmark_backend: str = os.getenv("OMNILIVE_LANDMARK_BACKEND", "auto").strip().lower()
    face_landmark_model: str = os.getenv(
        "OMNILIVE_FACE_LANDMARK_MODEL", "landmarks/face_landmark_5.onnx"
    )
    face_alignment_enabled: bool = _bool("OMNILIVE_FACE_ALIGNMENT_ENABLED", False)
    face_alignment_size: int = int(os.getenv("OMNILIVE_FACE_ALIGNMENT_SIZE", "256"))
    person_detector_backend: str = os.getenv("OMNILIVE_PERSON_DETECTOR_BACKEND", "auto").strip().lower()
    person_detector_model: str = os.getenv(
        "OMNILIVE_PERSON_DETECTOR_MODEL", "detectors/person_yolo.onnx"
    )
    person_detector_confidence: float = float(os.getenv("OMNILIVE_PERSON_DETECTOR_CONFIDENCE", "0.40"))
    person_detector_nms: float = float(os.getenv("OMNILIVE_PERSON_DETECTOR_NMS", "0.45"))
    swapper_model: str = os.getenv(
        "OMNILIVE_SWAPPER_MODEL", "swapper/inswapper_128_fp16.onnx"
    )
    arcface_model: str = os.getenv(
        "OMNILIVE_ARCFACE_MODEL", "recognition/w600k_r50.onnx"
    )
    enhancer_model: str = os.getenv(
        "OMNILIVE_ENHANCER_MODEL", "enhancer/gpen_bfr_256.onnx"
    )
    enhancer_enabled: bool = _bool("OMNILIVE_ENHANCER_ENABLED", True)
    enhancer_strength: float = float(os.getenv("OMNILIVE_ENHANCER_STRENGTH", "0.4"))
    swapper_engine: str = os.getenv("OMNILIVE_SWAPPER_ENGINE", "inswapper").strip().lower()
    xseg_model: str = os.getenv(
        "OMNILIVE_XSEG_MODEL", "occluder/xseg_2.onnx"
    )
    xseg_enabled: bool = _bool("OMNILIVE_XSEG_ENABLED", True)
    region_padding: float = float(os.getenv("OMNILIVE_REGION_PADDING", "0.22"))
    tracker_min_confidence: float = float(os.getenv("OMNILIVE_TRACKER_MIN_CONFIDENCE", "0.22"))
    processor_context_ttl_seconds: int = int(os.getenv("OMNILIVE_PROCESSOR_CONTEXT_TTL_SECONDS", "30"))
    localized_effects: tuple[str, ...] = _csv("OMNILIVE_LOCALIZED_EFFECTS", "custom_onnx")


settings = Settings()

if settings.is_production and settings.session_secret == "development-only-secret":
    raise RuntimeError("OMNILIVE_SESSION_SECRET must be set when OMNILIVE_PRODUCTION=1")

if settings.is_production and len(settings.session_secret) < 32:
    raise RuntimeError("OMNILIVE_SESSION_SECRET must be at least 32 characters in production")
