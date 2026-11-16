#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

PREFIX="${PREFIX_DEFAULT}"
CONFIG_PATH="${CONFIG_DEFAULT}"
DATA_DIR="${DATA_DIR_DEFAULT}"
ROLLBACK_ON_FAILURE=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix) PREFIX="${2:?missing value}"; shift 2 ;;
    --config) CONFIG_PATH="${2:?missing value}"; shift 2 ;;
    --data-dir) DATA_DIR="${2:?missing value}"; shift 2 ;;
    --no-rollback) ROLLBACK_ON_FAILURE=0; shift ;;
    -h|--help)
      echo "Usage: upgrade.sh [--prefix PATH] [--config PATH] [--data-dir PATH] [--no-rollback]"
      exit 0
      ;;
    *) die "unknown option: $1" ;;
  esac
done

require_root
require_source
[[ -d "${PREFIX}" ]] || die "no current installation at ${PREFIX}; use install.sh"
[[ -f "${CONFIG_PATH}" ]] || die "config not found: ${CONFIG_PATH}"

BACKUP_ROOT="${DATA_DIR}/backups/upgrade-$(date +%Y%m%d-%H%M%S)-$$"
mkdir -p "${BACKUP_ROOT}"
chmod 700 "${BACKUP_ROOT}"
tar -C "$(dirname "${PREFIX}")" -czf "${BACKUP_ROOT}/code.tar.gz" "$(basename "${PREFIX}")"
cp -a "${CONFIG_PATH}" "${BACKUP_ROOT}/config.json"
if [[ -f "/etc/systemd/system/${SERVICE_NAME}.service" ]]; then
  cp -a "/etc/systemd/system/${SERVICE_NAME}.service" "${BACKUP_ROOT}/service"
fi
if [[ -f "/etc/systemd/system/${ROTATE_SERVICE_NAME}.service" ]]; then
  cp -a "/etc/systemd/system/${ROTATE_SERVICE_NAME}.service" "${BACKUP_ROOT}/rotate-service"
fi
if [[ -f "/etc/systemd/system/${ROTATE_TIMER_NAME}" ]]; then
  cp -a "/etc/systemd/system/${ROTATE_TIMER_NAME}" "${BACKUP_ROOT}/rotate-timer"
fi
if [[ -f "${PREFIX}/.release.json" ]]; then
  cp -a "${PREFIX}/.release.json" "${BACKUP_ROOT}/release.json"
fi

PREVIOUS_VERSION=""
if [[ -f "${PREFIX}/.release.json" ]]; then
  # Early test installations may contain an empty or otherwise invalid
  # metadata file. Treat that as an unknown version instead of blocking the
  # upgrade or printing a traceback.
  if release_output="$(python3 - "${PREFIX}/.release.json" <<'PY' 2>/dev/null
import json
import sys
print(json.load(open(sys.argv[1], encoding="utf-8")).get("version", ""))
PY
)"; then
    PREVIOUS_VERSION="${release_output}"
  fi
fi
log "backed up current release to ${BACKUP_ROOT}"

rollback() {
  [[ ${ROLLBACK_ON_FAILURE} -eq 1 ]] || exit 1
  warn "upgrade failed; restoring ${BACKUP_ROOT}"
  systemctl stop "${SERVICE_NAME}.service" >/dev/null 2>&1 || true
  systemctl stop "${ROTATE_TIMER_NAME}" "${ROTATE_SERVICE_NAME}.service" >/dev/null 2>&1 || true
  rm -rf "${PREFIX}"
  tar -C "$(dirname "${PREFIX}")" -xzf "${BACKUP_ROOT}/code.tar.gz"
  cp -a "${BACKUP_ROOT}/config.json" "${CONFIG_PATH}"
  rm -f "/etc/systemd/system/${ROTATE_SERVICE_NAME}.service" \
    "/etc/systemd/system/${ROTATE_TIMER_NAME}"
  if [[ -f "${BACKUP_ROOT}/service" ]]; then
    cp -a "${BACKUP_ROOT}/service" "/etc/systemd/system/${SERVICE_NAME}.service"
  fi
  if [[ -f "${BACKUP_ROOT}/rotate-service" ]]; then
    cp -a "${BACKUP_ROOT}/rotate-service" \
      "/etc/systemd/system/${ROTATE_SERVICE_NAME}.service"
  fi
  if [[ -f "${BACKUP_ROOT}/rotate-timer" ]]; then
    cp -a "${BACKUP_ROOT}/rotate-timer" "/etc/systemd/system/${ROTATE_TIMER_NAME}"
  fi
  systemctl daemon-reload
  systemctl enable "${SERVICE_NAME}.service" >/dev/null 2>&1 || true
  if [[ -f "${BACKUP_ROOT}/rotate-timer" ]]; then
    systemctl enable "${ROTATE_TIMER_NAME}" >/dev/null 2>&1 || true
    systemctl restart "${ROTATE_TIMER_NAME}" >/dev/null 2>&1 || true
  fi
  systemctl restart "${SERVICE_NAME}.service" || true
  die "upgrade failed and rollback was applied"
}
trap rollback ERR

systemctl stop "${SERVICE_NAME}.service"
install_args=(
  --prefix "${PREFIX}"
  --config "${CONFIG_PATH}"
  --data-dir "${DATA_DIR}"
  --backup-root "${BACKUP_ROOT}"
)
if [[ -n "${PREVIOUS_VERSION}" ]]; then
  install_args+=(--previous-version "${PREVIOUS_VERSION}")
fi
"${SCRIPT_DIR}/install.sh" "${install_args[@]}"
trap - ERR
log "upgrade complete; backup retained at ${BACKUP_ROOT}"
