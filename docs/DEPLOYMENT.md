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

For visual detector / landmark selection:

```text
OMNILIVE_MODEL_ID
OMNILIVE_DETECTOR_BACKEND=auto
OMNILIVE_FACE_DETECTOR_MODEL=detectors/scrfd_2.5g.onnx
OMNILIVE_FACE_DETECTOR_CONFIDENCE=0.50
OMNILIVE_FACE_DETECTOR_NMS=0.40
OMNILIVE_LANDMARK_BACKEND=auto
OMNILIVE_FACE_LANDMARK_MODEL=landmarks/face_landmark_5.onnx
OMNILIVE_FACE_ALIGNMENT_ENABLED=false
OMNILIVE_FACE_ALIGNMENT_SIZE=256
OMNILIVE_PERSON_DETECTOR_MODEL=detectors/person_yolo.onnx
```

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


## Diagnostics and metrics

Runtime diagnostics remain available through:

```text
GET /health
GET /v1/model
GET /v1/telemetry
GET /metrics
```

`/metrics` is Prometheus text format and exports processor/model/input-age latency, stale-frame and inference counters, active streams, adaptive neural/crop state, model-load state, and memory gauges when available.


## GPU container preload

The Modal L4 startup now calls the same global processor used by live WebRTC and preloads:
- the configured visual ONNX runtime
- the configured SCRFD face detector when its file exists
- the configured semantic face landmark model when its file exists

The optional person detector stays lazy because it is not part of the current live face pipeline and should not consume GPU memory before it is needed.

Preload failures remain visible through `/health`, `/v1/model`, and vision diagnostics; missing optional detector/landmark weights still fall back to the existing baseline where configured.
