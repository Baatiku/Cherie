# Implementation ledger

## Phase 1 — foundation
- [x] repository repurposed as OmniLive
- [x] Android project and foreground service
- [x] session registry and expiry
- [x] Modal L4 ASGI service
- [x] tests and CI definitions

## Phase 2 — realtime transport
- [x] Android WebRTC + Camera2 capture
- [x] SDP offer/answer via aiortc
- [x] returned video track and Android renderer
- [x] dynamic STUN/TURN configuration
- [x] runtime backend URL
- [x] WebRTC RTT/loss/frame telemetry
- [x] backend media latency window
- [x] exponential reconnect/backoff
- [x] adaptive camera resolution/FPS from RTT and loss
- [x] EGL renderer cleanup
- [ ] incorporate backend inference p95 into adaptive controller
- [ ] TURN deployment near target users

## Phase 3 — GPU visual processor
- [ ] GPU-resident processing interface
- [ ] ONNX Runtime CUDA/TensorRT image
- [ ] detector + landmark tracker
- [ ] temporal crop tracking
- [ ] visual-effects model adapter
- [ ] masks/color correction/enhancement
- [ ] inference p50/p95 telemetry

## Phase 4 — rooted Android lab adapter
- [ ] isolated adapter module
- [ ] local Surface provider
- [ ] explicit test-app scoping
- [ ] lifecycle recovery/fallback

## Phase 5 — hardening
- [ ] thermal/battery handling
- [ ] GPU worker retry
- [ ] metrics dashboard
- [ ] 30-minute soak test
