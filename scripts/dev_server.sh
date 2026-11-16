#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export AX_MODEL_MANAGER_MODEL_ROOT="${AX_MODEL_MANAGER_MODEL_ROOT:-$ROOT/fixtures/models/generated}"
export AX_MODEL_MANAGER_TOKEN="${AX_MODEL_MANAGER_TOKEN:-local-dev-token}"

if [[ ! -d "$AX_MODEL_MANAGER_MODEL_ROOT" ]]; then
  "$ROOT/scripts/create_fixtures.py" "$AX_MODEL_MANAGER_MODEL_ROOT"
fi

exec "$ROOT/.venv/bin/uvicorn" ax_model_manager.app:app \
  --app-dir "$ROOT/backend" \
  --host "${AX_MODEL_MANAGER_HOST:-127.0.0.1}" \
  --port "${AX_MODEL_MANAGER_PORT:-18090}" \
  --reload
