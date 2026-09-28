#!/usr/bin/env bash
set -euo pipefail

PACKAGE="${OMNILIVE_PACKAGE:-com.baatiku.omnilive}"
ACTIVITY="${OMNILIVE_ACTIVITY:-.MainActivity}"
SERVICE="${OMNILIVE_SERVICE:-.stream.LiveStreamService}"
APK="${1:-build-artifacts/omnilive-debug.apk}"
BACKEND_URL="${OMNILIVE_BACKEND_URL:-}"
PROFILE="${OMNILIVE_PROFILE:-balanced}"
EFFECT="${OMNILIVE_EFFECT:-original}"
START_SERVICE="${OMNILIVE_START_SERVICE:-1}"
WAIT_SECONDS="${OMNILIVE_SMOKE_WAIT_SECONDS:-5}"
OUT_DIR="${OMNILIVE_SMOKE_OUT_DIR:-build-artifacts}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
REPORT="${OUT_DIR}/device-smoke-${STAMP}.txt"

command -v adb >/dev/null 2>&1 || {
  echo "adb is required but was not found in PATH" >&2
  exit 2
}

[[ -f "${APK}" ]] || {
  echo "APK not found: ${APK}" >&2
  exit 2
}

mapfile -t DEVICES < <(adb devices | awk 'NR>1 && $2=="device" {print $1}')
if [[ "${#DEVICES[@]}" -ne 1 ]]; then
  echo "Expected exactly one ready adb device, found ${#DEVICES[@]}." >&2
  adb devices >&2
  exit 2
fi

SERIAL="${DEVICES[0]}"
ADB=(adb -s "${SERIAL}")
mkdir -p "${OUT_DIR}"

{
  echo "OmniLive device smoke"
  echo "utc=${STAMP}"
  echo "serial=${SERIAL}"
  echo "apk=${APK}"
  echo "package=${PACKAGE}"
  echo "profile=${PROFILE}"
  echo "effect=${EFFECT}"
  echo "backend=${BACKEND_URL:-<not supplied>}"
  echo
  "${ADB[@]}" shell getprop ro.product.manufacturer | tr -d '\r' | sed 's/^/manufacturer=/'
  "${ADB[@]}" shell getprop ro.product.model | tr -d '\r' | sed 's/^/model=/'
  "${ADB[@]}" shell getprop ro.build.version.release | tr -d '\r' | sed 's/^/android=/'
  "${ADB[@]}" shell getprop ro.build.version.sdk | tr -d '\r' | sed 's/^/api=/'
  if "${ADB[@]}" shell 'command -v su >/dev/null 2>&1 && su -c id' 2>/dev/null | grep -q 'uid=0'; then
    echo "root=available"
  else
    echo "root=unavailable"
  fi
} > "${REPORT}"

echo "==> Installing ${APK}"
"${ADB[@]}" install -r -t "${APK}" | tee -a "${REPORT"

echo "==> Granting CAMERA"
"${ADB[@]}" shell pm grant "${PACKAGE}" android.permission.CAMERA || true

echo "==> Clearing logcat and launching activity"
"${ADB[@]}" logcat -c
"${ADB[@]}" shell am force-stop "${PACKAGE}"
"${ADB[@]}" shell am start -W -n "${PACKAGE}/${ACTIVITY}" | tee -a "${REPORT}"
sleep 2

PID="$("${ADB[@]}" shell pidof "${PACKAGE}" | tr -d '\r' || true)"
if [[ -z "${PID}" ]]; then
  echo "FAIL: app process is not running after launch" | tee -a "${REPORT}"
  "${ADB[@]}" logcat -d -v threadtime | tail -n 300 >> "${REPORT}"
  exit 1
fi
echo "pid=${PID}" >> "${REPORT}"

if [[ "${START_SERVICE}" == "1" ]]; then
  echo "==> Starting camera foreground service"
  SERVICE_ARGS=(
    shell am start-foreground-service
    -n "${PACKAGE}/${SERVICE}"
    --es profile "${PROFILE}"
    --es effect "${EFFECT}"
  )
  if [[ -n "${BACKEND_URL}" ]]; then
    SERVICE_ARGS+=(--es backend_url "${BACKEND_URL}")
  fi
  "${ADB[@]}" "${SERVICE_ARGS[@]}" | tee -a "${REPORT}"
fi

sleep "${WAIT_SECONDS}"

echo "==> Checking runtime state"
"${ADB[@]}" shell dumpsys activity services "${PACKAGE}" >> "${REPORT}" 2>&1 || true
"${ADB[@]}" shell dumpsys package "${PACKAGE}" | grep -E   'versionName=|versionCode=|android.permission.CAMERA|FOREGROUND_SERVICE' >> "${REPORT}" 2>&1 || true

LOGS="$("${ADB[@]}" logcat -d -v threadtime)"
printf '%s\n' "${LOGS}" | grep -E   "AndroidRuntime|FATAL EXCEPTION|SecurityException|ForegroundServiceDidNotStart|ForegroundServiceStartNotAllowed|CameraAccessException|com\.baatiku\.omnilive"   >> "${REPORT}" || true

if printf '%s\n' "${LOGS}" | grep -Eqi   'FATAL EXCEPTION|ForegroundServiceDidNotStart|ForegroundServiceStartNotAllowed|SecurityException.*(camera|foreground|com\.baatiku\.omnilive)'; then
  echo "FAIL: crash or foreground-service security failure detected" | tee -a "${REPORT}"
  echo "Report: ${REPORT}"
  exit 1
fi

if [[ "${START_SERVICE}" == "1" ]] && ! "${ADB[@]}" shell dumpsys activity services "${PACKAGE}" | grep -q 'LiveStreamService'; then
  echo "FAIL: LiveStreamService is not active after start request" | tee -a "${REPORT}"
  echo "Report: ${REPORT}"
  exit 1
fi

echo "PASS: install, launch and foreground-service smoke checks completed" | tee -a "${REPORT}"
echo "Report: ${REPORT}"
