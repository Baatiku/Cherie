# Implementation ledger

## Phase 1 — foundation
- [x] repository repurposed as OmniLive
- [x] Android project and foreground service
- [x] adaptive quality / frame-age controls
- [x] session registry and expiry
- [x] Modal L4 worker skeleton
- [x] CI and tests

## Phase 2 — realtime transport
- [x] current WebRTC Android SDK
- [x] Android PeerConnection + Camera2 capture
- [x] SDP offer/answer endpoint
- [x] backend aiortc PeerConnection
- [x] returned video track
- [ ] TURN configuration
- [ ] Android returned-track renderer/local Surface
- [ ] WebRTC RTT/loss stats
- [ ] reconnect/backoff state machine

## Phase 3 — GPU face processor
- [ ] FaceFusion-compatible assets
- [ ] ONNX Runtime CUDA/TensorRT
- [ ] detector + landmark tracker
- [ ] identity embedding cache
- [ ] swap model
- [ ] masks/color correction/enhancer
- [ ] temporal inference reuse

## Phase 4 — rooted Android lab adapter
- [ ] isolated adapter module
- [ ] local Surface provider
- [ ] explicit test-app scoping
- [ ] lifecycle recovery/fallback

## Phase 5 — hardening
- [ ] adaptive bitrate
- [ ] thermal/battery handling
- [ ] GPU worker retry
- [ ] metrics dashboard
- [ ] 30-minute soak test
