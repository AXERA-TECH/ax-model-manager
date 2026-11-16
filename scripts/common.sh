#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
VERSION="$(cat "${SOURCE_DIR}/VERSION")"
PREFIX_DEFAULT=/opt/axera-model-manager
CONFIG_DEFAULT=/etc/axera/model-manager/config.json
DATA_DIR_DEFAULT=/var/lib/axera-model-manager
SERVICE_NAME=axera-model-manager
ROTATE_SERVICE_NAME="${SERVICE_NAME}-rotate"
ROTATE_TIMER_NAME="${ROTATE_SERVICE_NAME}.timer"

log() { printf '[ax-model-manager] %s\n' "$*"; }
warn() { printf '[ax-model-manager] WARNING: %s\n' "$*" >&2; }
die() { printf '[ax-model-manager] ERROR: %s\n' "$*" >&2; exit 1; }

require_root() {
  [[ ${EUID} -eq 0 ]] || die "this script must run as root"
}

require_source() {
  [[ -d "${SOURCE_DIR}/backend/ax_model_manager" ]] || die "source tree not found: ${SOURCE_DIR}"
  [[ -f "${SOURCE_DIR}/VERSION" ]] || die "VERSION file not found"
}

python_bin() {
  printf '%s\n' "$1/venv/bin/python"
}

write_release_metadata() {
  local prefix="$1" backup_root="$2" previous_version="${3:-}"
  local commit=""
  if command -v git >/dev/null 2>&1 && git -C "${SOURCE_DIR}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    commit="$(git -C "${SOURCE_DIR}" rev-parse HEAD)"
  fi
  "${prefix}/venv/bin/python" - "$prefix/.release.json" "$VERSION" "$commit" "$backup_root" "$previous_version" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

path, version, commit, backup_root, previous = sys.argv[1:]
value = {
    "version": version,
    "commit": commit or None,
    "installed_at": datetime.now(timezone.utc).isoformat(),
    "backup_root": backup_root or None,
    "previous_version": previous or None,
}

path = Path(path)
path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
}
