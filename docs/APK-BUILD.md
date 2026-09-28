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
