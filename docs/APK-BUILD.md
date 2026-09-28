# Remote Android APK builder

This Dockerfile is an isolated build utility for producing an installable OmniLive debug APK when GitHub-hosted Actions are unavailable.

It does not run the OmniLive backend and does not participate in the live WebRTC/Modal architecture.

Build stages:
1. Gradle 8.9 + JDK 17
2. Android command-line tools
3. Android API 35 / build-tools 35.0.0
4. app + rootlab unit tests
5. `:app:assembleDebug`
6. tiny HTTP container serving only the generated `omnilive-debug.apk`

Railway can point an isolated service at:

```text
ops/apk-builder.Dockerfile
```

The service should be removed or put to sleep after install testing; it is not a production OmniLive service.


## Verified Render Free build path

The preferred install-test builder is now the direct private-repository Render service:

- service: `omnilive-apk-builder`
- source: `Baatiku/Cherie`
- branch: `main`
- build command: `bash ops/render-apk-build.sh`
- start command: `python -m http.server $PORT --bind 0.0.0.0 --directory build-artifacts`
- plan: Render Free

The first successful direct build was produced from commit `c12214a69a274ba3b911ea2bcd73959a16485bc0` after the AndroidX, OkHttp API-35 compatibility, Java 17 target alignment, and JUnit fixes.

The build passed:

```text
:app:testDebugUnitTest
:rootlab:testDebugUnitTest
:app:assembleDebug
```

Artifact:

```text
/omnilive-debug.apk
```

Verified build SHA-256:

```text
a6c6af3343548d25c774d7a3ca17b5e39cb3babf88b538b6a6742cce1e3455b5
```

The Render service is a build/artifact host only. It is not the OmniLive realtime backend.


## Latest verified diagnostic build

The current install-test build was produced from:

```text
9663b24fcb5a06436506f4a73bb73013cb267d1a
```

It includes the Android 0.6.0 device/backend preflight UI, build identity, camera-permission diagnostics, reference-image status, debug-only local HTTP support, and a build-configurable default backend URL.

The build passed:

```text
:app:testDebugUnitTest
:rootlab:testDebugUnitTest
:app:assembleDebug
BUILD SUCCESSFUL
```

Current APK SHA-256:

```text
c33406549985b4cca7229363b14576d0390590a96bf57420a538796cc5c56f5b
```

Artifact URL:

```text
https://omnilive-apk-builder.onrender.com/omnilive-debug.apk
```

To embed a deployed HTTPS backend into a future APK build, set:

```text
OMNILIVE_DEFAULT_BACKEND_URL=https://<backend-host>
```

If it is unset, the debug/development fallback remains `http://10.0.2.2:8000`; users can also edit the Backend URL directly in the app before starting a session.


## Thermal-aware diagnostic build

Verified source:

```text
975351f7f1c4424c37539b066a637c7f94c0c1cc
```

This build adds OS thermal-status-aware quality protection on top of the existing network/backend adaptation. Android thermal status is used instead of arbitrary battery-temperature cutoffs:

- moderate: caps the session at `balanced`
- severe / critical / emergency / shutdown: forces `low_latency`
- none / light: allows recovery, but only after a healthy hold window
- network RTT/loss/backend-p95 adaptation still applies under the thermal ceiling

The build passed the app tests, rootlab tests and `:app:assembleDebug`.

SHA-256:

```text
68dbf6f83f291215108bad12b783c4e09f4969cd27c662b16ed0aac5611f7cd6
```


## Latest phone-test build

Application source:

```text
9688e1edbee7ec086dac4e055713b3044f5d7b20
```

This build includes the thermal-aware adaptive controller, active thermal-cap telemetry, device/backend preflight, the staging-backend build default, reference-image status, and a copyable live diagnostic report that includes the active thermal quality ceiling.

Verified tasks:

```text
:app:testDebugUnitTest
:rootlab:testDebugUnitTest
:app:assembleDebug
BUILD SUCCESSFUL
```

SHA-256:

```text
963e92760ae1d74fcbce67e86c67ef841a46e4da2eb32dd183b12171c8b29a03
```

The Render APK builder currently injects:

```text
OMNILIVE_DEFAULT_BACKEND_URL=https://omnilive-backend-staging.onrender.com
```

The staging backend is a CPU transport/install-test target only. Production reference-aware GPU inference still belongs on the Modal L4 deployment.


## Verified dual normal/rootlab build

Source commit:

```text
98dbaada7b8b6fd61b22796209d41e4d5c9138a8
```

The Render APK gate now performs:

```text
:app:testDebugUnitTest
:rootlab:testDebugUnitTest
:app:assembleDebug
:app:assembleDebug -Pomnilive.rootLab=true
```

Both app variants compiled successfully.

Normal APK:

```text
https://omnilive-apk-builder.onrender.com/omnilive-debug.apk
SHA-256 718b3c49449370139efddf358a81f8b09c9327c7086d64a9af5c067bb16a6d0e
```

Rooted-lab APK:

```text
https://omnilive-apk-builder.onrender.com/omnilive-rootlab-debug.apk
SHA-256 a72c758c19a74a8a85c5be5f02ab2ba550e29c427e524cd9922e85e9a7472a29
```

The rootlab APK contains the isolated lab runtime, real `su` capability probe, processed-frame lifecycle bridge, bounded camera rebind/recovery coordinator, physical-camera fallback path, and a Zoom package preset. It does not yet contain the final rooted/device-specific camera redirection adapter that makes another app consume OmniLive frames; that remains the next rootlab implementation phase.


## Verified transport-preflight dual build

Application source:

```text
1b97d2522ed179f2d3c8d7496238d5b92d7d56c0
```

This build upgrades the Android preflight from a health-only request to a disposable end-to-end transport check. It verifies backend health, ICE/TURN configuration, authenticated session creation/validation, a real WebSocket upgrade and `/v1/live` probe, then deletes the disposable session.

Verified tasks:

```text
:app:testDebugUnitTest
:rootlab:testDebugUnitTest
:app:assembleDebug
:app:assembleDebug -Pomnilive.rootLab=true
BUILD SUCCESSFUL
```

Backend CI on the same source passed:

```text
51 passed
```

Normal APK:

```text
https://omnilive-apk-builder.onrender.com/omnilive-debug.apk
SHA-256 fb43f7d144730a950ecf720dde09fee182db4b1787d9ddf8b7d4b4007f1ddfa7
```

Rooted-lab APK:

```text
https://omnilive-apk-builder.onrender.com/omnilive-rootlab-debug.apk
SHA-256 f608dd11c74e19a311cffffdb50f2cffb029b5ce46a848741c7e96799ac4933d
```


## Zoom surface-broker rootlab build

Application source:

```text
c5bd80527d3ca06fe1ffe1a5c0f6e5b2f2ad9047
```

This build adds the first concrete Zoom package-specific rootlab transport:

- Zoom-scoped modern libxposed Camera2 open/session/close hooks
- target-process Messenger client for Camera2 output `Surface` objects
- rootlab-only exported OmniLive broker service
- sender UID validation against `us.zoom.videomeetings`
- shared WebRTC EGL context registry
- processed-frame `SurfaceFrameOutput` attach attempts against transferred Zoom surfaces
- rootlab smoke-harness checks for root, broker packaging, hook logs and successful surface attachment

The Xposed hook remains fail-open for this checkpoint: the physical Camera2 call still proceeds until real-device testing tells us whether Zoom/vendor buffer queues permit the processed producer attach.

Verified Render tasks:

```text
:app:testDebugUnitTest
:rootlab:testDebugUnitTest
:app:assembleDebug
:app:assembleDebug -Pomnilive.rootLab=true
BUILD SUCCESSFUL
```

Normal APK:

```text
https://omnilive-apk-builder.onrender.com/omnilive-debug.apk
SHA-256 33a7459c88c4bcbb7a11f509c829e6cb60c7fc80f0ecd2e53d7e37cdb3551be6
```

Rootlab APK:

```text
https://omnilive-apk-builder.onrender.com/omnilive-rootlab-debug.apk
SHA-256 baa35e69b12bc0e8f5289cb80b9bd9b880fcf20028c4626e5148a7efe6f35b17
```


## Zoom Camera2 redirection device-validation build

Application source:

```text
1a2bccee1a342c141efa8e0966ade374739c3de6
```

This rootlab checkpoint moves beyond surface observation into an actual readiness-gated Camera2 substitution path for the Zoom package:

- the broker reports redirect readiness only when the shared WebRTC EGL context exists and returned frames are live
- `CaptureRequest.Builder.addTarget/removeTarget` surfaces and Camera2 session outputs are substituted as a coupled mapping
- the physical camera is redirected to continuously drained PRIVATE `ImageReader` surfaces
- Zoom's original output surfaces are reserved for OmniLive returned-frame rendering
- legacy raw `Surface` lists plus `OutputConfiguration` and `SessionConfiguration` paths are supported
- common post-create session mutation calls are mapped to the redirected outputs
- pre-session physical/redirect decisions are sticky across multi-target request construction
- an already-redirected session keeps its mapping across a temporary returned-stream gap instead of mixing physical and redirected targets
- the broker logs the first successful EGL swap into every transferred target surface
- the device smoke harness can require hook activation, session redirection, and actual returned-frame presentation independently

Verified Render tasks:

```text
:app:testDebugUnitTest
:rootlab:testDebugUnitTest
:app:assembleDebug
:app:assembleDebug -Pomnilive.rootLab=true
BUILD SUCCESSFUL
```

Normal APK:

```text
https://omnilive-apk-builder.onrender.com/omnilive-debug.apk
SHA-256 c552d8a200e87c6cba4b6a3adf25029e3f7a6e989273e33afffcabe7b9f91b02
```

Rooted-lab APK:

```text
https://omnilive-apk-builder.onrender.com/omnilive-rootlab-debug.apk
SHA-256 084938924ae3b759cf4da131cad5f84a4b5e8f489037946e5a37c3c686f678c8
```

For the rooted Zoom proof run, use the rootlab APK and enable the module for `us.zoom.videomeetings`. The strongest smoke gate is:

```text
OMNILIVE_ROOTLAB_MODE=1
OMNILIVE_ROOTLAB_LAUNCH_TARGET=1
OMNILIVE_ROOTLAB_REQUIRE_HOOKS=1
OMNILIVE_ROOTLAB_REQUIRE_REDIRECT=1
OMNILIVE_ROOTLAB_REQUIRE_FRAME_PRESENTED=1
```

The source implementation is complete enough for this device gate, but the build result alone does not prove that Zoom displays the returned OmniLive frames on a particular rooted/OEM device. That remains the next external validation step.
