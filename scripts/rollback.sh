#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

PREFIX="${PREFIX_DEFAULT}"
CONFIG_PATH="${CONFIG_DEFAULT}"
DATA_DIR="${DATA_DIR_DEFAULT}"
BACKUP=""
LIST_ONLY=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix) PREFIX="${2:?missing value}"; shift 2 ;;
    --config) CONFIG_PATH="${2:?missing value}"; shift 2 ;;
    --data-dir) DATA_DIR="${2:?missing value}"; shift 2 ;;
    --backup) BACKUP="${2:?missing value}"; shift 2 ;;
    --list) LIST_ONLY=1; shift ;;
    *) die "unknown option: $1" ;;
  esac
done

require_root
BACKUP_ROOT="${DATA_DIR}/backups"
if [[ ${LIST_ONLY} -eq 1 ]]; then
  find "${BACKUP_ROOT}" -mindepth 1 -maxdepth 1 -type d -name 'upgrade-*' -printf '%f\n' 2>/dev/null | sort
  exit 0
fi

if [[ -z "${BACKUP}" ]]; then
  BACKUP="$(find "${BACKUP_ROOT}" -mindepth 1 -maxdepth 1 -type d -name 'upgrade-*' 2>/dev/null | sort | tail -n 1)"
fi
[[ -n "${BACKUP}" ]] || die "no upgrade backup found"
[[ -f "${BACKUP}/code.tar.gz" ]] || die "invalid backup: ${BACKUP}"

systemctl stop "${SERVICE_NAME}.service" "${ROTATE_TIMER_NAME}" "${ROTATE_SERVICE_NAME}.service" \
  >/dev/null 2>&1 || true
rm -rf "${PREFIX}"
tar -C "$(dirname "${PREFIX}")" -xzf "${BACKUP}/code.tar.gz"
cp -a "${BACKUP}/config.json" "${CONFIG_PATH}"
rm -f \
  "/etc/systemd/system/${ROTATE_SERVICE_NAME}.service" \
  "/etc/systemd/system/${ROTATE_TIMER_NAME}"
if [[ -f "${BACKUP}/service" ]]; then
  cp -a "${BACKUP}/service" "/etc/systemd/system/${SERVICE_NAME}.service"
fi
if [[ -f "${BACKUP}/rotate-service" ]]; then
  cp -a "${BACKUP}/rotate-service" \
    "/etc/systemd/system/${ROTATE_SERVICE_NAME}.service"
fi
if [[ -f "${BACKUP}/rotate-timer" ]]; then
  cp -a "${BACKUP}/rotate-timer" "/etc/systemd/system/${ROTATE_TIMER_NAME}"
fi
systemctl daemon-reload
systemctl enable "${SERVICE_NAME}.service" >/dev/null 2>&1 || true
if [[ -f "${BACKUP}/rotate-timer" ]]; then
  systemctl enable "${ROTATE_TIMER_NAME}" >/dev/null 2>&1 || true
fi
if [[ -f "${PREFIX}/logrotate_axera-model-manager" ]]; then
  install -m 0644 "${PREFIX}/logrotate_axera-model-manager" /etc/logrotate.d/axera-model-manager
fi
systemctl restart "${SERVICE_NAME}.service"
if [[ -f "/etc/systemd/system/${ROTATE_TIMER_NAME}" ]]; then
  systemctl restart "${ROTATE_TIMER_NAME}"
fi

MANAGER_PORT="$(python3 - "${CONFIG_PATH}" <<'PY'
import json
import sys
print(json.load(open(sys.argv[1], encoding="utf-8"))["manager_port"])
PY
)"
for _ in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:${MANAGER_PORT}/health" >/dev/null 2>&1; then
    log "restored backup ${BACKUP}; service is healthy"
    exit 0
  fi
  sleep 1
done

warn "restored service did not become healthy; recent journal follows"
journalctl -u "${SERVICE_NAME}.service" -n 80 --no-pager || true
die "rollback health check failed"
