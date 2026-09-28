# Implementation ledger

## Phase 1 — foundation
- [x] repository repurposed as OmniLive
- [x] Android project and foreground service
- [x] session registry and expiry
- [x] Modal L4 ASGI service
- [x] named Modal runtime Secret attached to the L4 function
- [x] tests and CI definitions
- [x] explicit Gradle 8.9 CI toolchain
- [x] Kotlin/Compose compiler plugin aligned to 2.2.21 for Gradle 8.9 / AGP 8.7.x compatibility
- [x] lightweight CPU test dependencies separated from GPU image

## Phase 2 — realtime transport
- [x] Android WebRTC + Camera2 capture
- [x] SDP offer/answer via aiortc
- [x] returned video track and Android renderer
- [x] dynamic STUN/TURN configuration
- [x] TURN configuration applied on both Android and backend aiortc peers
- [x] coturn-compatible expiring TURN REST credentials
- [x] persistent WebSocket live signalling that pins the Modal GPU input
- [x] worker-local backend telemetry over the live WebSocket
- [x] runtime backend URL
- [x] WebRTC RTT/loss/frame telemetry
- [x] exponential reconnect/backoff
- [x] Android Wi-Fi/cellular/offline transition recovery
- [x] adaptive resolution/FPS from RTT, loss, and backend p95
- [x] adaptive profile updates propagated to the pinned backend session
- [x] latest-frame decoded input slot; stale unconsumed frames are overwritten, not queued
- [x] backend media/input-age/processor latency windows
- [x] EGL renderer cleanup
- [x] frame-correlated capture-to-returned-sink timing with p50/p95 and correlation-loss counters
- [x] explicit sender bitrate/framerate limits tied to the existing adaptive quality profile
- [x] actual send/receive bitrate, encoder FPS, and available-outgoing-bitrate telemetry
- [x] selected ICE candidate-pair type/protocol and relay/direct telemetry
- [ ] production TURN deployment near target users
- [ ] real-device network transition and 30-minute transport soak test
- [ ] optional renderer-present timestamp refinement beyond renderer-input timing

## Phase 3 — GPU visual processor
- [x] GPU-resident processor contract
- [x] processor p50/p95 timing
- [x] Modal Volume mounted at /models
- [x] ONNX Runtime GPU dependency in Modal image
- [x] CPU ONNX runtime for local/CI tests
- [x] TensorRT/CUDA/CPU provider selection
- [x] lazy model cache + cold-start preload
- [x] generic NCHW/NHWC image-to-image adapter
- [x] built-in effects
- [x] per-session effect selection
- [x] Android effect controls including custom ONNX model selection
- [x] model diagnostics and synthetic benchmark
- [x] replaceable detector interface
- [x] zero-download OpenCV face detector baseline
- [x] sparse landmark/feature tracker between detector passes
- [x] detector cadence including empty-scene cadence control
- [x] region padding/clamping and crop-only invocation
- [x] feathered region composite
- [x] custom-model illumination/color matching
- [x] short-lived temporal transform cache
- [x] cache invalidation after tracking confidence collapse
- [x] independent neural inference FPS budget
- [x] overload-driven neural FPS, detector interval, and crop-size adaptation
- [x] per-stage detector/tracker/model/compositor/total timing
- [x] dropped-inference, stale-input overwrite, cache-reuse, reset, and error counters
- [x] GPU-capable SCRFD face detector adapter with lazy ONNX loading and Haar fallback
- [x] lazy YOLO-style ONNX person detector adapter with letterboxing/NMS and common export parsing
- [x] semantic facial-landmark adapter and optional aligned localized inference
- [x] fixed-crop + overload-adaptation benchmark harness using the live processor path
- [ ] real custom model loaded from Modal Volume
- [ ] Modal L4 live-model latency benchmark results

## Phase 4 — rooted Android lab adapter
- [x] generic ProcessedFrameOutput boundary below ReturnedVideoBus
- [x] GPU-friendly local processed-frame Surface producer with size-1 pending-frame renderer
- [x] isolated rooted lab module with explicit Gradle opt-in and no normal-app dependency by default
- [x] package-scoped compatibility lifecycle/state-machine boundary
- [ ] package-specific camera compatibility implementation
- [ ] camera lifecycle recovery/fallback

The rooted lab phase remains isolated from transport/model code. The generic output boundary and Surface producer are now ready for an isolated lab adapter, but no root-specific code has been mixed into normal OmniLive.

## Phase 5 — hardening
- [x] Android battery/charging/temperature and thermal-state telemetry
- [ ] measured thermal-driven quality policy
- [ ] GPU worker retry/reallocation
- [ ] metrics dashboard/export
- [ ] 30-minute end-to-end soak test
- [ ] repeated Wi-Fi/cellular handoff test
- [ ] production TURN failover test

## Current external gates

The core software can proceed without model weights, but real performance validation requires:
1. a Modal `omnilive-runtime` Secret and a deployed endpoint from current main
2. a reachable TURN service or managed TURN credentials
3. a real Android test device
4. a real ONNX model in the `omnilive-models` Volume for custom-model benchmarks
