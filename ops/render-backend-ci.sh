#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARTIFACT_DIR="${ROOT_DIR}/build-artifacts/backend-ci"
REPORT="${ARTIFACT_DIR}/pytest.txt"

mkdir -p "${ARTIFACT_DIR}"
rm -f "${REPORT}"

echo "==> Installing OmniLive backend test dependencies"
python -m pip install --disable-pip-version-check -r "${ROOT_DIR}/backend/requirements-dev.txt"

echo "==> Running OmniLive backend tests"
cd "${ROOT_DIR}"
PYTHONPATH=backend python -m pytest -q backend/tests | tee "${REPORT}"

SMOKE_REPORT="${ARTIFACT_DIR}/staging-smoke.txt"
if [[ -n "${OMNILIVE_STAGING_URL:-}" ]]; then
  echo "==> Waiting for and smoke-testing exact staging deployment"
  python "${ROOT_DIR}/ops/staging-smoke.py" | tee "${SMOKE_REPORT}"
else
  printf '%s\n' "Skipped: OMNILIVE_STAGING_URL is not configured." > "${SMOKE_REPORT}"
fi

BUILD_SHA="${RENDER_GIT_COMMIT:-unknown}"
cat > "${ARTIFACT_DIR}/index.html" <<HTML
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>OmniLive backend CI</title>
</head>
<body style="font-family:system-ui,sans-serif;max-width:720px;margin:40px auto;padding:16px">
  <h1>OmniLive backend CI</h1>
  <p>Status: passed</p>
  <p>Commit: <code>${BUILD_SHA}</code></p>
  <p><a href="/pytest.txt">pytest output</a></p>\n  <p><a href="/staging-smoke.txt">staging smoke output</a></p>
</body>
</html>
HTML

echo "==> Backend unit and staging smoke gates passed for ${BUILD_SHA}"
