# Deployment

## Modal GPU service

From the repository root:

```bash
cd backend
pip install modal
modal setup
modal deploy modal_app.py
```

The current Modal function:
- uses an L4
- mounts `omnilive-models` at `/models`
- preloads the configured ONNX model
- has a 3600-second input timeout
- uses `@modal.concurrent(max_inputs=1)`
- exposes FastAPI through `@modal.asgi_app()`

The Android live path uses `/v1/live` as a persistent WebSocket. Modal treats that WebSocket as a live function input, so the GPU container remains pinned to the session rather than relying on post-offer idle lifetime.

The short HTTP endpoints remain in the same L4 service for the MVP. Splitting cheap control traffic onto CPU is deferred until real latency/cost measurements justify the additional routing layer.

Modal WebSocket reference:
https://modal.com/blog/websocket-launch

Modal concurrency reference:
https://modal.com/docs/sdk/py/latest/concurrent

## Required secrets/config

The deployed function attaches the named Modal Secret `omnilive-runtime`. Create it before the first production deployment; `OMNILIVE_SESSION_SECRET` is required so the remote service cannot silently fall back to the development secret.

Example:

```bash
modal secret create omnilive-runtime \
  OMNILIVE_SESSION_SECRET=<long-random-value> \
  OMNILIVE_MODEL_ID=none \
  OMNILIVE_ICE_URLS=stun:stun.cloudflare.com:3478
```

Add TURN values to the same secret when the relay is ready. Update the named secret rather than committing credentials to Git.

Optional values stored in `omnilive-runtime`.

For static ICE credentials:

```text
OMNILIVE_ICE_USERNAME
OMNILIVE_ICE_CREDENTIAL
```

For coturn-compatible TURN REST credentials:

```text
OMNILIVE_TURN_SHARED_SECRET
OMNILIVE_TURN_CREDENTIAL_TTL_SECONDS=900
OMNILIVE_TURN_USER=omnilive
```

When `OMNILIVE_TURN_SHARED_SECRET` is set, `GET /v1/ice` generates a timestamped short-lived HMAC credential and never exposes the shared secret itself. The same ICE provider configures both Android and the backend aiortc peer.

## TURN deployment for Nigerian testing

See `docs/NETWORKING.md`.

The first latency comparison should test:
1. direct/STUN where possible
2. managed Anycast TURN
3. coturn deployed in the AWS Lagos Local Zone

Do not assume the nearest named region is fastest; measure ICE candidate type, RTT, packet loss, and returned-video behavior on the actual mobile networks.

## Current production gate

A source-complete deployment is not the same as a verified live path. Before calling the transport production-ready, verify on a real device:
- `wss://.../v1/live` remains open for at least 30 minutes
- a relay candidate succeeds on restrictive mobile NAT
- backend and Android select a working candidate pair
- returned frames continue while neural inference is deliberately overloaded
- Wi-Fi to cellular and cellular to Wi-Fi transitions recover
- no processor queue grows under overload
