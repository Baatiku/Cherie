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
- [x] Android gallery reference-image picker, preview, replace/remove controls and private local normalization
- [x] session-affine Android reference-image upload over the pinned live WebSocket
- [x] backend reference-image validation, normalization, bounded worker-local storage and metadata acknowledgement
- [x] reference-image state disposal when the pinned live session closes
- [x] opt-in dual-image ONNX adapter for compatible live-crop + reference-image models
- [x] GPU-resident processor contract
- [x] processor p50/p95 timing
- [x] Modal Volume mounted at /models
- [x] ONNX Runtime GPU dependency in Modal image
- [x] CPU ONNX runtime for local/CI tests
- [x] TensorRT/CUDA/CPU provider selection
- [x] GPU execution-provider quarantine/reload with alternate-provider failover and bounded recovery cooldown
- [x] recovery telemetry plus frame-level inference suppression/cache reuse while the model runtime is cooling down
- [x] lazy model cache + L4 startup preload for visual model and available face/landmark runtimes
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
- [x] OS thermal-status-driven quality caps with gradual post-heat recovery
- [x] ONNX provider-level failure recovery/quarantine with controlled retry cooldown
- [ ] Modal GPU worker retry/reallocation after worker/container loss
- [x] Prometheus-format backend metrics export
- [ ] metrics dashboard
- [ ] 30-minute end-to-end soak test
- [ ] repeated Wi-Fi/cellular handoff test
- [ ] production TURN failover test

## Current build/install checkpoint

- [x] private-repo Render Free APK builder connected directly to `Baatiku/Cherie:main`
- [x] dedicated Render Free backend-CI runner executes the CPU backend test suite
- [x] AndroidX enabled for the Android project
- [x] OkHttp pinned to a compileSdk 35-compatible release
- [x] Java/Kotlin JVM targets aligned to Java 17 in app and rootlab modules
- [x] JUnit configured for app and rootlab unit tests
- [x] Android device preflight shows app/build/device/API/camera/rootlab state and can copy diagnostics
- [x] Android backend preflight calls `/health` before attempting a live session
- [x] debug-only cleartext traffic support retained for local HTTP development endpoints
- [x] release/default backend URL is build-configurable with `OMNILIVE_DEFAULT_BACKEND_URL`
- [x] latest Render build passed `:app:testDebugUnitTest :rootlab:testDebugUnitTest :app:assembleDebug`
- [x] latest install-test APK generated from source commit `975351f7f1c4424c37539b066a637c7f94c0c1cc`
- [x] thermal-aware adaptive controller caps moderate heat at balanced and severe-or-worse at low_latency, then requires a healthy recovery window before upgrading
- [x] latest APK SHA-256: `68dbf6f83f291215108bad12b783c4e09f4969cd27c662b16ed0aac5611f7cd6`
- [x] backend CI passed 49 tests on the same source commit (one upstream Starlette/TestClient deprecation warning remains)

## Current external gates

The core source implementation can continue without external assets. Remaining live validation requires:
1. installation and launch testing of the generated debug APK on a real Android device
2. a Modal `omnilive-runtime` Secret and a deployed endpoint from current main
3. a reachable TURN service or managed TURN credentials for relay benchmarking
4. a real ONNX model in the `omnilive-models` Volume for custom-model benchmarks

For reference-aware models, `OMNILIVE_MODEL_ADAPTER=dual_image` supports exactly two 4D image inputs. Models requiring embeddings, masks or latent inputs need a dedicated adapter.
