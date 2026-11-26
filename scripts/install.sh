#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

PREFIX="${PREFIX_DEFAULT}"
CONFIG_PATH="${CONFIG_DEFAULT}"
DATA_DIR="${DATA_DIR_DEFAULT}"
MODEL_ROOT=/mnt/models
MANAGER_PORT=18090
TOKEN=""
NO_TOKEN=0
TEMPMON_INSTALL=1
BACKUP_ROOT=""
PREVIOUS_VERSION=""
NO_SERVICE=0

usage() {
  cat <<'USAGE'
Usage: install.sh [options]

Options:
  --prefix PATH        install prefix       (default: /opt/axera-model-manager)
  --config PATH        config path          (default: /etc/axera/model-manager/config.json)
  --data-dir PATH      state/log directory  (default: /var/lib/axera-model-manager)
  --model-root PATH    model root
  --port NUMBER        manager port
  --token TOKEN        bearer token; generated when absent
  --no-token           disable bearer token auth (trusted networks only)
  --no-tempmon         do not install the bundled tempmon agent
  --backup-root PATH   upgrade metadata only
  --previous-version V upgrade metadata only
  --no-service         install files but do not start service
  -h, --help           show this help
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix) PREFIX="${2:?missing value}"; shift 2 ;;
    --config) CONFIG_PATH="${2:?missing value}"; shift 2 ;;
    --data-dir) DATA_DIR="${2:?missing value}"; shift 2 ;;
    --model-root) MODEL_ROOT="${2:?missing value}"; shift 2 ;;
    --port) MANAGER_PORT="${2:?missing value}"; shift 2 ;;
    --token) TOKEN="${2:?missing value}"; shift 2 ;;
    --no-token) NO_TOKEN=1; shift ;;
    --no-tempmon) TEMPMON_INSTALL=0; shift ;;
    --backup-root) BACKUP_ROOT="${2:?missing value}"; shift 2 ;;
    --previous-version) PREVIOUS_VERSION="${2:?missing value}"; shift 2 ;;
    --no-service) NO_SERVICE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

require_root
require_source
command -v python3 >/dev/null 2>&1 || die "python3 is required"
command -v systemctl >/dev/null 2>&1 || die "systemctl is required"
if [[ ${NO_SERVICE} -eq 0 ]] && ! command -v curl >/dev/null 2>&1; then
  die "curl is required for the health check"
fi

for path in "${PREFIX}" "${CONFIG_PATH}" "${DATA_DIR}" "${MODEL_ROOT}"; do
  case "${path}" in
    *[[:space:]\"\\]*|*$'\n'*) die "paths with whitespace or quotes are not supported: ${path}" ;;
  esac
done

mkdir -p "$(dirname "${CONFIG_PATH}")" "${DATA_DIR}" "$(dirname "${PREFIX}")"
chmod 700 "${DATA_DIR}"

if [[ -f "${CONFIG_PATH}" ]]; then
  log "preserving existing config: ${CONFIG_PATH}"
else
  if [[ ${NO_TOKEN} -eq 1 ]]; then
    TOKEN=""
  elif [[ -z "${TOKEN}" ]]; then
    TOKEN="$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(32))
PY
)"
  fi
  TOKEN="${TOKEN}" MODEL_ROOT="${MODEL_ROOT}" MANAGER_PORT="${MANAGER_PORT}" DATA_DIR="${DATA_DIR}" \
    python3 - "$CONFIG_PATH" <<'PY'
import json
import os
import sys
from pathlib import Path

path = Path(sys.argv[1])
value = {
    "model_root": os.environ["MODEL_ROOT"],
    "manager_port": int(os.environ["MANAGER_PORT"]),
    "llama_port_min": 18080,
    "llama_port_max": 18089,
    "data_dir": os.environ["DATA_DIR"],
    "read_only": False,
    "enable_control": True,
    "process_backend": "systemd",
    "max_running_models": 1,
    "min_free_cmm_mb": 1024,
    "min_os_available_mb": 1024,
    "max_npu_temp_c": 85.0,
    "auth_token": os.environ["TOKEN"],
    "allowed_origins": [],
}
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
os.chmod(path, 0o600)
PY
  log "created config: ${CONFIG_PATH}"
fi

install_bundled_tempmon() {
  [[ ${TEMPMON_INSTALL} -eq 1 ]] || return 0
  local existing=""
  if command -v tempmon >/dev/null 2>&1; then
    existing="$(command -v tempmon)"
  elif [[ -x /root/tempmon ]]; then
    existing="/root/tempmon"
  elif [[ -x /usr/local/bin/tempmon ]]; then
    existing="/usr/local/bin/tempmon"
  fi
  if [[ -n "${existing}" ]]; then
    log "tempmon already installed at ${existing}; skipping bundled agent"
    return 0
  fi
  local bundled="${PREFIX}/third_party/tempmon/tempmon"
  if [[ ! -x "${bundled}" ]]; then
    warn "bundled tempmon not found; install tempmon separately for resource checks"
    return 0
  fi
  install -m 0755 "${bundled}" /usr/local/bin/tempmon
  cat > /etc/systemd/system/tempmon.service <<'UNIT'
[Unit]
Description=tempmon board agent
After=network.target

[Service]
ExecStart=/usr/local/bin/tempmon 8080
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT
  systemctl daemon-reload
  systemctl enable tempmon.service >/dev/null
  systemctl restart tempmon.service
  log "installed bundled tempmon at /usr/local/bin/tempmon"
}

STAGING="${PREFIX}.new"
OLD_PREFIX="${PREFIX}.old"
HAD_OLD_PREFIX=0
PREFIX_REPLACED=0

install_exit_handler() {
  local status=$?
  trap - EXIT INT TERM
  rm -rf "${STAGING}"
  if [[ ${status} -ne 0 && ${PREFIX_REPLACED} -eq 1 ]]; then
    systemctl stop "${SERVICE_NAME}.service" >/dev/null 2>&1 || true
    systemctl stop "${ROTATE_TIMER_NAME}" "${ROTATE_SERVICE_NAME}.service" >/dev/null 2>&1 || true
    rm -rf "${PREFIX}"
    if [[ ${HAD_OLD_PREFIX} -eq 1 && -d "${OLD_PREFIX}" ]]; then
      mv "${OLD_PREFIX}" "${PREFIX}"
    fi
  else
    rm -rf "${OLD_PREFIX}"
  fi
  exit "${status}"
}
trap install_exit_handler EXIT

rm -rf "${STAGING}"
mkdir -p "${STAGING}"
cp -a "${SOURCE_DIR}/backend" "${STAGING}/backend"
cp -a "${SOURCE_DIR}/scripts" "${STAGING}/scripts"
if [[ -d "${SOURCE_DIR}/third_party" ]]; then
  cp -a "${SOURCE_DIR}/third_party" "${STAGING}/third_party"
fi
cp -a "${SOURCE_DIR}/README.md" "${SOURCE_DIR}/RELEASES.md" "${SOURCE_DIR}/VERSION" "${STAGING}/"

PYTHONPATH="${STAGING}/backend${PYTHONPATH:+:${PYTHONPATH}}" \
  python3 "${STAGING}/scripts/migrate_config.py" "${CONFIG_PATH}" >/dev/null
python3 - "${CONFIG_PATH}" > "${STAGING}/.install-env.sh" <<'PY'
import json
import shlex
import sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
for key in ("model_root", "data_dir"):
    print(f"{key.upper()}={shlex.quote(str(value[key]))}")
print(f"MANAGER_PORT={int(value['manager_port'])}")
PY
source "${STAGING}/.install-env.sh"
rm -f "${STAGING}/.install-env.sh"
for path in "${DATA_DIR}" "${MODEL_ROOT}"; do
  case "${path}" in
    *[[:space:]\"\\]*|*$'\n'*) die "configured paths with whitespace or quotes are not supported: ${path}" ;;
  esac
done
mkdir -p "${DATA_DIR}/logs"
chmod 700 "${DATA_DIR}" "${DATA_DIR}/logs"

cat > "${STAGING}/axera-model-manager.service" <<UNIT
[Unit]
Description=AXERA Model Manager API
Documentation=file:${PREFIX}/README.md
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=${PREFIX}
Environment=AX_MODEL_MANAGER_CONFIG=${CONFIG_PATH}
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=${PREFIX}/venv/bin/uvicorn ax_model_manager.app:app --app-dir ${PREFIX}/backend --host 0.0.0.0 --port ${MANAGER_PORT}
Restart=on-failure
RestartSec=3
TimeoutStopSec=20
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=${DATA_DIR} $(dirname "${CONFIG_PATH}")
ProtectHome=read-only
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectKernelLogs=true
ProtectControlGroups=true
ProtectClock=true
RestrictRealtime=true
RestrictSUIDSGID=true
LockPersonality=true
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6

[Install]
WantedBy=multi-user.target
UNIT

cat > "${STAGING}/axera-model-manager-rotate.service" <<UNIT
[Unit]
Description=AXERA Model Manager log rotation

[Service]
Type=oneshot
User=root
ExecStart=${PREFIX}/venv/bin/python ${PREFIX}/scripts/rotate_logs.py ${DATA_DIR}/logs
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=${DATA_DIR}
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectKernelLogs=true
ProtectControlGroups=true
ProtectClock=true
RestrictRealtime=true
RestrictSUIDSGID=true
LockPersonality=true
RestrictAddressFamilies=AF_UNIX

[Install]
WantedBy=multi-user.target
UNIT

cat > "${STAGING}/axera-model-manager-rotate.timer" <<TIMER
[Unit]
Description=Hourly AXERA Model Manager log rotation

[Timer]
OnBootSec=10min
OnUnitActiveSec=1h
AccuracySec=1min
Persistent=true
Unit=${ROTATE_SERVICE_NAME}.service

[Install]
WantedBy=timers.target
TIMER

cat > "${STAGING}/logrotate_axera-model-manager" <<ROTATE
${DATA_DIR}/logs/*.log {
    daily
    rotate 14
    maxsize 50M
    missingok
    notifempty
    compress
    delaycompress
    copytruncate
}
ROTATE

if [[ -d "${PREFIX}" ]]; then
  HAD_OLD_PREFIX=1
  rm -rf "${OLD_PREFIX}"
  mv "${PREFIX}" "${OLD_PREFIX}"
  PREFIX_REPLACED=1
fi
mv "${STAGING}" "${PREFIX}"
PREFIX_REPLACED=1

python3 -m venv "${PREFIX}/venv"
if compgen -G "${PREFIX}/third_party/wheelhouse/*.whl" >/dev/null; then
  log "installing Python dependencies from bundled wheelhouse"
  "${PREFIX}/venv/bin/pip" install --disable-pip-version-check --no-index \
    --find-links "${PREFIX}/third_party/wheelhouse" \
    -r "${PREFIX}/backend/requirements.txt"
else
  log "installing Python dependencies from PyPI"
  "${PREFIX}/venv/bin/pip" install --disable-pip-version-check --upgrade pip
  "${PREFIX}/venv/bin/pip" install --disable-pip-version-check -r "${PREFIX}/backend/requirements.txt"
fi

install -m 0644 "${PREFIX}/axera-model-manager.service" "/etc/systemd/system/${SERVICE_NAME}.service"
install -m 0644 "${PREFIX}/axera-model-manager-rotate.service" \
  "/etc/systemd/system/${ROTATE_SERVICE_NAME}.service"
install -m 0644 "${PREFIX}/axera-model-manager-rotate.timer" \
  "/etc/systemd/system/${ROTATE_TIMER_NAME}"
install -m 0644 "${PREFIX}/logrotate_axera-model-manager" /etc/logrotate.d/axera-model-manager
write_release_metadata "${PREFIX}" "${BACKUP_ROOT}" "${PREVIOUS_VERSION}"
systemctl daemon-reload
systemctl enable "${SERVICE_NAME}.service" >/dev/null
systemctl enable "${ROTATE_TIMER_NAME}" >/dev/null

if [[ ${NO_SERVICE} -eq 1 ]]; then
  log "files installed; service not started (--no-service)"
  exit 0
fi

systemctl restart "${SERVICE_NAME}.service"
systemctl restart "${ROTATE_TIMER_NAME}"
for _ in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:${MANAGER_PORT}/health" >/dev/null 2>&1; then
    install_bundled_tempmon || warn "bundled tempmon installation failed"
    log "installed ${VERSION} at ${PREFIX}; service is healthy"
    exit 0
  fi
  sleep 1
done

warn "service did not become healthy; recent journal follows"
journalctl -u "${SERVICE_NAME}.service" -n 80 --no-pager || true
die "installation health check failed"
