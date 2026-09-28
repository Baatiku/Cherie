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
- [x] exponential reconnect/backoff
- [x] adaptive resolution/FPS from RTT, loss and backend p95
- [x] backend media/processor latency windows
- [x] EGL renderer cleanup
- [ ] TURN deployment near target users

## Phase 3 — GPU visual processor
- [x] GPU-resident processor contract
- [x] processor p50/p95 timing
- [x] passthrough processor for transport benchmarks
- [ ] ONNX Runtime CUDA/TensorRT image
- [ ] detector + landmark tracker
- [ ] temporal crop tracking
- [ ] visual-effects model adapter
- [ ] masks/color correction/enhancement

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
