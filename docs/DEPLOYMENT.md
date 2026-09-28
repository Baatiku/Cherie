# Modal deployment

From the repository root:

```bash
cd backend
pip install modal
modal setup
modal deploy modal_app.py
```

Set secrets in Modal rather than committing them:
- OMNILIVE_SESSION_SECRET
- OMNILIVE_ICE_URLS
- OMNILIVE_ICE_USERNAME
- OMNILIVE_ICE_CREDENTIAL

For real mobile networks, configure TURN in addition to STUN. The Android app reads ICE configuration from `GET /v1/ice`.

The Modal ASGI service runs on an L4 and stays warm for 300 seconds after activity. This is deliberately simple for first latency measurements; later revisions should separate signalling/control from GPU media workers when multi-user packing is introduced.
