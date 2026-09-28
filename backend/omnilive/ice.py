from __future__ import annotations

import base64
import hashlib
import hmac
import time

from .config import settings
from .models import IceConfig, IceServer


def turn_rest_credentials(
    shared_secret: str,
    ttl_seconds: int,
    user: str = "omnilive",
    now: int | None = None,
) -> tuple[str, str, int]:
    if not shared_secret:
        raise ValueError("shared_secret is required")
    issued_at = int(time.time()) if now is None else int(now)
    ttl = max(60, int(ttl_seconds))
    expires_at = issued_at + ttl
    label = (user or "omnilive").strip().replace(":", "_")
    username = str(expires_at) + ":" + label
    digest = hmac.new(
        shared_secret.encode("utf-8"),
        username.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    credential = base64.b64encode(digest).decode("ascii")
    return username, credential, expires_at


def current_ice_config() -> IceConfig:
    urls = list(settings.ice_urls)
    if settings.turn_shared_secret:
        ttl = min(
            max(60, settings.turn_credential_ttl_seconds),
            max(60, settings.session_ttl_seconds),
        )
        username, credential, expires_at = turn_rest_credentials(
            settings.turn_shared_secret,
            ttl,
            settings.turn_user,
        )
        return IceConfig(
            servers=[IceServer(urls=urls, username=username, credential=credential)],
            credential_mode="rest_shared_secret",
            expires_at=expires_at,
        )
    return IceConfig(
        servers=[
            IceServer(
                urls=urls,
                username=settings.ice_username,
                credential=settings.ice_credential,
            )
        ],
        credential_mode="static",
        expires_at=None,
    )
