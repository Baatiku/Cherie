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
