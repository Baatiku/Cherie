#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

from websockets.sync.client import connect as websocket_connect


BASE_URL = os.environ.get("OMNILIVE_STAGING_URL", "").strip().rstrip("/")
EXPECTED_SHA = os.environ.get("RENDER_GIT_COMMIT", "").strip()
WAIT_SECONDS = int(os.environ.get("OMNILIVE_STAGING_WAIT_SECONDS", "180"))


def http_json(path: str, *, method: str = "GET", payload=None, token: str | None = None):
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(
        BASE_URL + path,
        data=body,
        method=method,
        headers=headers,
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        raw = response.read().decode("utf-8")
        if response.status < 200 or response.status >= 300:
            raise RuntimeError(f"{method} {path} returned HTTP {response.status}")
        return json.loads(raw) if raw else {}


def http_text(path: str) -> str:
    request = urllib.request.Request(BASE_URL + path, headers={"Accept": "text/plain"})
    with urllib.request.urlopen(request, timeout=45) as response:
        if response.status < 200 or response.status >= 300:
            raise RuntimeError(f"GET {path} returned HTTP {response.status}")
        return response.read().decode("utf-8")


def wait_for_exact_deploy() -> dict:
    deadline = time.monotonic() + max(1, WAIT_SECONDS)
    last_detail = "not attempted"
    while time.monotonic() < deadline:
        try:
            health = http_json("/health")
            deployed_sha = str(health.get("build_sha") or "")
            if EXPECTED_SHA and EXPECTED_SHA != "unknown" and deployed_sha != EXPECTED_SHA:
                last_detail = f"staging is {deployed_sha or 'unknown'}, waiting for {EXPECTED_SHA}"
            elif health.get("service") != "omnilive":
                last_detail = "unexpected staging service identity"
            else:
                return health
        except Exception as exc:
            last_detail = str(exc)
        time.sleep(4)
    raise RuntimeError("staging did not converge to the tested commit: " + last_detail)


def websocket_probe() -> dict:
    if BASE_URL.startswith("https://"):
        websocket_url = "wss://" + BASE_URL[len("https://"):] + "/v1/live"
    elif BASE_URL.startswith("http://"):
        websocket_url = "ws://" + BASE_URL[len("http://"):] + "/v1/live"
    else:
        raise RuntimeError("staging URL must use http:// or https://")

    with websocket_connect(websocket_url, open_timeout=30, close_timeout=5) as websocket:
        websocket.send(json.dumps({"action": "probe"}))
        response = json.loads(websocket.recv(timeout=30))
    if response.get("event") != "probe" or response.get("ok") is not True:
        raise RuntimeError("live WebSocket probe was not acknowledged: " + repr(response))
    return response


def main() -> int:
    if not BASE_URL:
        raise RuntimeError("OMNILIVE_STAGING_URL is required")

    health = wait_for_exact_deploy()

    metrics = http_text("/metrics")
    required_metrics = (
        "omnilive_processor_latency_ms",
        "omnilive_model_loaded",
        "omnilive_model_execution_failures_total",
    )
    missing = [metric for metric in required_metrics if metric not in metrics]
    if missing:
        raise RuntimeError("staging metrics missing: " + ", ".join(missing))

    ice = http_json("/v1/ice")
    servers = ice.get("servers") or []
    if not servers:
        raise RuntimeError("staging returned no ICE servers")

    session = http_json(
        "/v1/sessions",
        method="POST",
        payload={"profile": "balanced", "effect": "original"},
    )
    session_id = str(session.get("session_id") or "")
    token = str(session.get("token") or "")
    if not session_id or not token:
        raise RuntimeError("staging session response did not contain credentials")

    try:
        validated = http_json(
            f"/v1/sessions/{session_id}/validate",
            token=token,
        )
        if validated.get("ok") is not True:
            raise RuntimeError("staging session validation failed")

        ws = websocket_probe()
    finally:
        try:
            deleted = http_json(
                f"/v1/sessions/{session_id}",
                method="DELETE",
                token=token,
            )
            if deleted.get("ok") is not True:
                raise RuntimeError("staging disposable session was not deleted")
        except Exception as exc:
            print("session cleanup failed: " + str(exc), file=sys.stderr)
            raise

    relay = any(
        str(url).lower().startswith(("turn:", "turns:"))
        for server in servers
        for url in (server.get("urls") or [])
    )
    report = {
        "ok": True,
        "build_sha": health.get("build_sha"),
        "health_status": health.get("status"),
        "ice_server_count": len(servers),
        "relay_configured": relay,
        "credential_mode": ws.get("credential_mode"),
        "websocket": True,
        "session_auth": True,
        "session_cleanup": True,
        "metrics": True,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print("OmniLive staging smoke failed: " + str(exc), file=sys.stderr)
        raise
