FROM gradle:8.9-jdk17 AS build

USER root

ARG ANDROID_COMMANDLINE_TOOLS=15859902
ARG ANDROID_COMMANDLINE_TOOLS_SHA256=4e4c464f145a7512b57d088ac6c278c03c9eea610886b35a5e0804e74eedf583

ENV ANDROID_HOME=/opt/android-sdk
ENV ANDROID_SDK_ROOT=/opt/android-sdk
ENV PATH=/opt/android-sdk/cmdline-tools/latest/bin:/opt/android-sdk/platform-tools:$PATH

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates unzip wget \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p "$ANDROID_HOME/cmdline-tools" \
    && wget -q "https://dl.google.com/android/repository/commandlinetools-linux-${ANDROID_COMMANDLINE_TOOLS}_latest.zip" -O /tmp/android-tools.zip \
    && echo "${ANDROID_COMMANDLINE_TOOLS_SHA256}  /tmp/android-tools.zip" | sha256sum -c - \
    && unzip -q /tmp/android-tools.zip -d /tmp/android-tools \
    && mv /tmp/android-tools/cmdline-tools "$ANDROID_HOME/cmdline-tools/latest" \
    && rm -rf /tmp/android-tools /tmp/android-tools.zip \
    && yes | sdkmanager --licenses >/dev/null \
    && sdkmanager "platform-tools" "platforms;android-35" "build-tools;35.0.0"

WORKDIR /workspace
COPY android /workspace/android

WORKDIR /workspace/android

RUN gradle \
    :app:testDebugUnitTest \
    :rootlab:testDebugUnitTest \
    :app:assembleDebug \
    --stacktrace \
    --no-daemon

FROM python:3.12-slim

WORKDIR /srv
COPY --from=build /workspace/android/app/build/outputs/apk/debug/app-debug.apk /srv/omnilive-debug.apk

RUN printf '%s\n' \
    '<!doctype html>' \
    '<html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>OmniLive APK</title></head>' \
    '<body style="font-family:sans-serif;max-width:640px;margin:40px auto;padding:16px">' \
    '<h1>OmniLive debug APK</h1>' \
    '<p>This build is generated from the current OmniLive main branch for install testing.</p>' \
    '<p><a href="/omnilive-debug.apk">Download omnilive-debug.apk</a></p>' \
    '</body></html>' > /srv/index.html

CMD ["sh", "-c", "python -m http.server ${PORT:-8080} --directory /srv"]
