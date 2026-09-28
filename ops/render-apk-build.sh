#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CACHE_DIR="${HOME}/.cache/omnilive-apk"
TOOLS_DIR="${CACHE_DIR}/tools"
ANDROID_HOME="${CACHE_DIR}/android-sdk"
ANDROID_SDK_ROOT="${ANDROID_HOME}"
JDK_HOME="${TOOLS_DIR}/jdk17"
GRADLE_HOME="${TOOLS_DIR}/gradle-8.9"
ARTIFACT_DIR="${ROOT_DIR}/build-artifacts"

ANDROID_COMMANDLINE_TOOLS="15859902"
ANDROID_COMMANDLINE_TOOLS_SHA256="4e4c464f145a7512b57d088ac6c278c03c9eea610886b35a5e0804e74eedf583"

mkdir -p "${CACHE_DIR}" "${TOOLS_DIR}" "${ARTIFACT_DIR}" "${ANDROID_HOME}/cmdline-tools"

if [[ ! -x "${JDK_HOME}/bin/java" ]]; then
  echo "==> Downloading Temurin JDK 17"
  rm -rf "${JDK_HOME}"
  mkdir -p "${JDK_HOME}"
  curl -fsSL --retry 3 --retry-delay 2     "https://api.adoptium.net/v3/binary/latest/17/ga/linux/x64/jdk/hotspot/normal/eclipse"     -o "${CACHE_DIR}/jdk17.tar.gz"
  tar -xzf "${CACHE_DIR}/jdk17.tar.gz" -C "${JDK_HOME}" --strip-components=1
fi

if [[ ! -x "${GRADLE_HOME}/bin/gradle" ]]; then
  echo "==> Downloading Gradle 8.9"
  curl -fsSL --retry 3 --retry-delay 2     "https://services.gradle.org/distributions/gradle-8.9-bin.zip"     -o "${CACHE_DIR}/gradle-8.9-bin.zip"
  curl -fsSL --retry 3 --retry-delay 2     "https://services.gradle.org/distributions/gradle-8.9-bin.zip.sha256"     -o "${CACHE_DIR}/gradle-8.9-bin.zip.sha256"
  printf '%s  %s\n' "$(cat "${CACHE_DIR}/gradle-8.9-bin.zip.sha256")" "${CACHE_DIR}/gradle-8.9-bin.zip" | sha256sum -c -
  rm -rf "${GRADLE_HOME}"
  unzip -q "${CACHE_DIR}/gradle-8.9-bin.zip" -d "${TOOLS_DIR}"
fi

if [[ ! -x "${ANDROID_HOME}/cmdline-tools/latest/bin/sdkmanager" ]]; then
  echo "==> Downloading Android command-line tools"
  curl -fsSL --retry 3 --retry-delay 2     "https://dl.google.com/android/repository/commandlinetools-linux-${ANDROID_COMMANDLINE_TOOLS}_latest.zip"     -o "${CACHE_DIR}/android-tools.zip"
  echo "${ANDROID_COMMANDLINE_TOOLS_SHA256}  ${CACHE_DIR}/android-tools.zip" | sha256sum -c -
  rm -rf "${ANDROID_HOME}/cmdline-tools/latest" "${CACHE_DIR}/android-tools"
  mkdir -p "${CACHE_DIR}/android-tools"
  unzip -q "${CACHE_DIR}/android-tools.zip" -d "${CACHE_DIR}/android-tools"
  mv "${CACHE_DIR}/android-tools/cmdline-tools" "${ANDROID_HOME}/cmdline-tools/latest"
fi

export JAVA_HOME="${JDK_HOME}"
export ANDROID_HOME ANDROID_SDK_ROOT
export ANDROID_USER_HOME="${CACHE_DIR}/android-user-home"
export PATH="${JAVA_HOME}/bin:${GRADLE_HOME}/bin:${ANDROID_HOME}/cmdline-tools/latest/bin:${ANDROID_HOME}/platform-tools:${PATH}"
export GRADLE_USER_HOME="${CACHE_DIR}/gradle-user-home"
export GRADLE_OPTS="-Dorg.gradle.daemon=false -Dorg.gradle.jvmargs=-Xmx1536m -XX:MaxMetaspaceSize=512m -Dfile.encoding=UTF-8"

mkdir -p "${ANDROID_USER_HOME}" "${GRADLE_USER_HOME}"

echo "==> Installing Android API 35 / build-tools 35.0.0"
yes | sdkmanager --licenses >/dev/null || true
sdkmanager "platform-tools" "platforms;android-35" "build-tools;35.0.0"

echo "sdk.dir=${ANDROID_HOME}" > "${ROOT_DIR}/android/local.properties"

echo "==> Running OmniLive Android tests and debug build"
cd "${ROOT_DIR}/android"
gradle   :app:testDebugUnitTest   :rootlab:testDebugUnitTest   :app:assembleDebug   --stacktrace   --no-daemon

APK_PATH="${ROOT_DIR}/android/app/build/outputs/apk/debug/app-debug.apk"
if [[ ! -s "${APK_PATH}" ]]; then
  echo "APK was not produced at ${APK_PATH}" >&2
  exit 1
fi

cp "${APK_PATH}" "${ARTIFACT_DIR}/omnilive-debug.apk"
(
  cd "${ARTIFACT_DIR}"
  sha256sum omnilive-debug.apk > omnilive-debug.apk.sha256
)

cat > "${ARTIFACT_DIR}/index.html" <<'HTML'
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>OmniLive Debug APK</title>
</head>
<body style="font-family:system-ui,sans-serif;max-width:640px;margin:40px auto;padding:16px">
  <h1>OmniLive debug APK</h1>
  <p>Built from the current OmniLive main branch for install testing.</p>
  <p><a href="/omnilive-debug.apk">Download omnilive-debug.apk</a></p>
  <p><a href="/omnilive-debug.apk.sha256">SHA-256 checksum</a></p>
</body>
</html>
HTML

echo "==> APK ready: ${ARTIFACT_DIR}/omnilive-debug.apk"
cat "${ARTIFACT_DIR}/omnilive-debug.apk.sha256"
