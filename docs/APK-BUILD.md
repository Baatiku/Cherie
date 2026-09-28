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
