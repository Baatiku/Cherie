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
- [x] rootlab runtime composition with real `su` capability probe, Android retry scheduler, Zoom target preset and processed-frame bridge
- [x] separate rootlab-enabled APK build gate (`-Pomnilive.rootLab=true`) so lab-only app sources compile in CI
- [x] package-specific Zoom Camera2 compatibility source implementation
  - [x] Zoom-scoped libxposed Camera2 lifecycle/session hooks
  - [x] cross-process Zoom output-Surface handoff over rootlab-only Messenger broker
  - [x] WebRTC-shared EGL context handoff and returned-frame Surface renderer
  - [x] readiness handshake requiring shared EGL plus a live returned WebRTC stream before a new redirect is selected
  - [x] Camera2 request-target substitution paired with session-output substitution
  - [x] drained PRIVATE ImageReader dummy outputs for the physical Camera2 producer
  - [x] legacy `List<Surface>`, `OutputConfiguration` and `SessionConfiguration` session redirection
  - [x] post-create `prepare`, `tearDown`, `updateOutputConfiguration` and `finalizeOutputConfigurations` argument substitution
  - [x] sticky physical/redirect session selection across multi-surface request construction and temporary returned-stream gaps
  - [x] one-shot EGL swap logging so device smoke can prove an OmniLive returned frame reached a transferred Zoom Surface
  - [ ] rooted-device validation that Zoom actually displays the returned OmniLive frames
- [x] isolated rootlab camera lifecycle recovery/rebind policy with bounded retries and physical-camera fallback

The rooted lab phase remains isolated from transport/model code. Normal APKs use the normal manifest and do not package the exported broker component. Rootlab APKs use a rootlab-only manifest plus Zoom/Xposed broker classes. Before a Camera2 session commits to redirection, unsupported/not-ready paths remain fail-open on the physical camera. Once a request/session commits to redirected surfaces, the mapping stays consistent across temporary returned-stream gaps so Camera2 request targets cannot diverge from the configured session. Real Zoom/vendor behavior remains a rooted-device validation gate.

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
- [x] Android transport preflight validates `/health`, ICE/TURN, disposable session authentication, live WebSocket upgrade/probe and session disposal before device testing
- [x] debug-only cleartext traffic support retained for local HTTP development endpoints
- [x] release/default backend URL is build-configurable with `OMNILIVE_DEFAULT_BACKEND_URL`
- [x] latest Render build passed `:app:testDebugUnitTest :rootlab:testDebugUnitTest :app:assembleDebug`
- [x] latest install-test APKs generated from application source commit `1b97d2522ed179f2d3c8d7496238d5b92d7d56c0`
- [x] thermal-aware adaptive controller caps moderate heat at balanced and severe-or-worse at low_latency, then requires a healthy recovery window before upgrading
- [x] copied live diagnostics include connection/ICE/network/frame/backend/bitrate/device/reference/error state plus the active thermal quality cap
- [x] latest normal APK SHA-256: `fb43f7d144730a950ecf720dde09fee182db4b1787d9ddf8b7d4b4007f1ddfa7`
- [x] latest rootlab redirection normal APK SHA-256: `c552d8a200e87c6cba4b6a3adf25029e3f7a6e989273e33afffcabe7b9f91b02`
- [x] latest rootlab redirection APK SHA-256: `084938924ae3b759cf4da131cad5f84a4b5e8f489037946e5a37c3c686f678c8`
- [x] latest rootlab redirection APKs generated from application source commit `1a2bccee1a342c141efa8e0966ade374739c3de6`
- [x] rootlab device smoke can independently require hook activation, Camera2 redirection and first returned-frame presentation to a Zoom target Surface
- [x] backend CI passed 51 tests on application source commit `1b97d2522ed179f2d3c8d7496238d5b92d7d56c0` (one upstream Starlette/TestClient deprecation warning remains)
- [x] zero-cost CPU staging backend is live at `https://omnilive-backend-staging.onrender.com` for install/session/WebRTC transport testing; it is not the production Modal GPU backend

## Current external gates

The core source implementation can continue without external assets. Remaining live validation requires:
1. installation and launch testing of the generated debug APK on a real Android device
2. a Modal `omnilive-runtime` Secret and a deployed endpoint from current main
3. a reachable TURN service or managed TURN credentials for relay benchmarking
4. a real ONNX model in the `omnilive-models` Volume for custom-model benchmarks

For reference-aware models, `OMNILIVE_MODEL_ADAPTER=dual_image` supports exactly two 4D image inputs. Models requiring embeddings, masks or latent inputs need a dedicated adapter.
