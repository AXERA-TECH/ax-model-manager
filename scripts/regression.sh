#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
BASE="${1:-http://127.0.0.1:18090}"
TOKEN="${AX_MODEL_MANAGER_TOKEN:-}"
EXPECTED_VERSION="${AX_MODEL_MANAGER_VERSION:-$(cat "${SOURCE_DIR}/VERSION")}"
pass_count=0
fail_count=0
skip_count=0

passed() {
  printf 'PASS %s\n' "$1"
  pass_count=$((pass_count + 1))
}

failed() {
  printf 'FAIL %s\n' "$1" >&2
  fail_count=$((fail_count + 1))
}

skipped() {
  printf 'SKIP %s\n' "$1"
  skip_count=$((skip_count + 1))
}

command -v curl >/dev/null 2>&1 || {
  printf 'ERROR curl is required\n' >&2
  exit 2
}

api_body() {
  local path="$1"
  shift
  curl --fail --silent --show-error --max-time 20 "$@" "${BASE}${path}"
}

status_code() {
  local path="$1"
  shift
  curl --silent --show-error --max-time 20 \
    --output /dev/null --write-out '%{http_code}' "$@" "${BASE}${path}"
}

check_body() {
  local name="$1" path="$2" pattern="$3"
  shift 3
  local body=""
  if body="$(api_body "${path}" "$@")" && grep -q "${pattern}" <<<"${body}"; then
    passed "${name}"
  else
    failed "${name}"
  fi
}

check_body "health" "/health" '"status":"ok"'
check_body "health-version" "/health" "\"version\":\"${EXPECTED_VERSION}\""

if curl --fail --silent --show-error --max-time 20 \
  "${BASE}/api/v1/models" >/dev/null 2>&1; then
  failed "unauthorized-models-rejected"
else
  passed "unauthorized-models-rejected"
fi

if [[ -n "${TOKEN}" ]]; then
  auth=(-H "Authorization: Bearer ${TOKEN}")
  check_body "authorized-config" "/api/v1/config" 'model_root' "${auth[@]}"
  check_body "authorized-models" "/api/v1/models" '"models"' "${auth[@]}"
  check_body "authorized-runtimes" "/api/v1/runtimes" 'reconciled_at' "${auth[@]}"
  check_body "authorized-audit" "/api/v1/audit" '"records"' "${auth[@]}"

  code="$(status_code "/api/v1/models/missing" "${auth[@]}")"
  if [[ "${code}" == "404" ]]; then
    passed "missing-model-404"
  else
    printf 'FAIL missing-model-404 (got %s)\n' "${code}" >&2
    fail_count=$((fail_count + 1))
  fi
else
  skipped "authorized APIs (AX_MODEL_MANAGER_TOKEN is empty)"
fi

check_body "web-ui" "/" 'AXERA Model Manager'
check_body "web-js" "/ui/app.js" 'renderDeviceResources'

printf 'Regression: %d passed, %d failed, %d skipped\n' \
  "${pass_count}" "${fail_count}" "${skip_count}"
[[ ${fail_count} -eq 0 ]]
