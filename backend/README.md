# Modal backend

The backend follows Modal's serverless WebRTC pattern and uses Pipecat SmallWebRTCTransport.

## Required secret

    modal secret create omnicam-config OMNICAM_API_TOKEN=<long-random-value>

Optional TURN values can be placed in the same secret:

    TURN_URLS=turn:example.com:3478,turns:example.com:5349
    TURN_USERNAME=...
    TURN_CREDENTIAL=...

TURN is strongly recommended for carrier-grade NAT/mobile networks.

## Face model

No third-party face-swap weights are committed.

Create the persistent volume:

    modal volume create omnicam-models

Place a compatible, legally obtained InsightFace swap model at:

    /inswapper_128.onnx

The detector model is cached under /models/insightface.

If the swap model is absent, output intentionally remains passthrough except for the AI marker. This lets transport and virtual-camera latency be tested before model provisioning.

## Deploy

    modal deploy backend/modal_app.py

Use the HTTPS URL of the Signaling web class in the Android app.

## Scaling

The initial policy is one live call per L4. After measuring GPU utilization and p95 frame latency, test two simultaneous calls per L4 rather than assuming concurrency is safe.
