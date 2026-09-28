# Implementation ledger

## Phase 1 — foundation
- [x] repository repurposed as OmniLive
- [x] Android project
- [x] streaming foreground service
- [x] Camera2 abstraction
- [x] adaptive quality controller
- [x] frame-age gate
- [x] backend session registry
- [x] token expiry
- [x] frame freshness/order policy
- [x] Modal L4 worker skeleton
- [x] health/session APIs
- [x] backend tests
- [x] CI

## Phase 2 — realtime transport
- [ ] Android WebRTC native dependency
- [ ] signalling endpoint
- [ ] TURN configuration
- [ ] camera source -> WebRTC track
- [ ] returned track -> decoder Surface
- [ ] RTT/loss telemetry

## Phase 3 — GPU face processor
- [ ] FaceFusion-compatible assets
- [ ] ONNX Runtime CUDA/TensorRT
- [ ] detector
- [ ] tracker
- [ ] identity embedding cache
- [ ] swap model
- [ ] occlusion mask
- [ ] color correction
- [ ] optional enhancer

## Phase 4 — rooted Android lab adapter
- [ ] isolated adapter module
- [ ] local Surface provider
- [ ] explicit test-app scoping
- [ ] camera lifecycle recovery
- [ ] physical-camera fallback

## Phase 5 — hardening
- [ ] reconnect logic
- [ ] adaptive bitrate
- [ ] thermal/battery handling
- [ ] GPU worker retry
- [ ] metrics dashboard
- [ ] 30-minute soak test
