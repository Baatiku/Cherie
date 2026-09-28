import os
from dataclasses import dataclass

@dataclass(frozen=True)
class Settings:
    session_secret: str = os.getenv("OMNILIVE_SESSION_SECRET", "development-only-secret")
    session_ttl_seconds: int = int(os.getenv("OMNILIVE_SESSION_TTL_SECONDS", "900"))
    max_frame_age_ms: int = int(os.getenv("OMNILIVE_MAX_FRAME_AGE_MS", "140"))
    target_fps: int = int(os.getenv("OMNILIVE_TARGET_FPS", "30"))
    model_id: str = os.getenv("OMNILIVE_MODEL_ID", "faceswap-dev")

settings = Settings()
