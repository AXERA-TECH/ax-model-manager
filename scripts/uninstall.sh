#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

PREFIX="${PREFIX_DEFAULT}"
CONFIG_PATH="${CONFIG_DEFAULT}"
DATA_DIR="${DATA_DIR_DEFAULT}"
STOP_RUNTIMES=0
PURGE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix) PREFIX="${2:?missing value}"; shift 2 ;;
    --config) CONFIG_PATH="${2:?missing value}"; shift 2 ;;
    --data-dir) DATA_DIR="${2:?missing value}"; shift 2 ;;
    --stop-runtimes) STOP_RUNTIMES=1; shift ;;
    --purge) PURGE=1; shift ;;
    *) die "unknown option: $1" ;;
  esac
done

require_root
if [[ ${STOP_RUNTIMES} -eq 1 ]]; then
  mapfile -t units < <(systemctl list-units 'ax-model-*.service' --no-legend --plain | awk '{print $1}')
  if [[ ${#units[@]} -gt 0 ]]; then
    systemctl stop "${units[@]}"
  fi
else
  warn "preserving model runtimes; use --stop-runtimes to stop ax-model-* units"
fi

systemctl stop "${SERVICE_NAME}.service" "${ROTATE_TIMER_NAME}" "${ROTATE_SERVICE_NAME}.service" \
  >/dev/null 2>&1 || true
systemctl disable "${SERVICE_NAME}.service" "${ROTATE_TIMER_NAME}" >/dev/null 2>&1 || true
rm -f \
  "/etc/systemd/system/${SERVICE_NAME}.service" \
  "/etc/systemd/system/${ROTATE_SERVICE_NAME}.service" \
  "/etc/systemd/system/${ROTATE_TIMER_NAME}" \
  /etc/logrotate.d/axera-model-manager
systemctl daemon-reload
rm -rf "${PREFIX}"

if [[ ${PURGE} -eq 1 ]]; then
  rm -rf "${DATA_DIR}"
  rm -f "${CONFIG_PATH}"
  # Only remove an empty parent; a custom config path may live beside other
  # system files, so never recursively delete its parent directory.
  rmdir --ignore-fail-on-non-empty "$(dirname "${CONFIG_PATH}")" || true
  log "uninstalled and purged state/config"
else
  log "uninstalled; preserved config=${CONFIG_PATH} data=${DATA_DIR}"
fi
