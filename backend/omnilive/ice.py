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


def _is_turn_url(url: str) -> bool:
    lowered = url.strip().lower()
    return lowered.startswith("turn:") or lowered.startswith("turns:")


def current_ice_config() -> IceConfig:
    urls = [u for u in settings.ice_urls if u.strip()]
    if not urls:
        urls = ["stun:stun.l.google.com:19302"]
    stun_urls = [u for u in urls if not _is_turn_url(u)]
    turn_urls = [u for u in urls if _is_turn_url(u)]
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
        servers: list[IceServer] = []
        if stun_urls:
            # STUN never needs TURN REST credentials.
            servers.append(IceServer(urls=stun_urls))
        if turn_urls:
            for url in turn_urls:
                servers.append(IceServer(urls=[url], username=username, credential=credential))
        if not servers:
            for url in urls:
                servers.append(IceServer(urls=[url], username=username, credential=credential))
        return IceConfig(
            servers=servers,
            credential_mode="rest_shared_secret",
            expires_at=expires_at,
        )
    if turn_urls or stun_urls:
        servers = []
        if stun_urls:
            servers.append(IceServer(urls=stun_urls))
        if turn_urls:
            for url in turn_urls:
                servers.append(
                    IceServer(
                        urls=[url],
                        username=settings.ice_username,
                        credential=settings.ice_credential,
                    )
                )
        return IceConfig(
            servers=servers,
            credential_mode="static",
            expires_at=None,
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
