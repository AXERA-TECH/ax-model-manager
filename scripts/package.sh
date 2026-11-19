#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
VERSION="$(cat "${SOURCE_DIR}/VERSION")"
OUTPUT="${SOURCE_DIR}/dist/ax-model-manager-${VERSION}.tar.gz"

mkdir -p "$(dirname "${OUTPUT}")"
tar -czf "${OUTPUT}" \
  --exclude='.git' \
  --exclude='.venv' \
  --exclude='dist' \
  --exclude='docs-internal' \
  --exclude='__pycache__' \
  --exclude='.pytest_cache' \
  --exclude='*.egg-info' \
  --exclude='.secrets' \
  --exclude='fixtures/models/generated' \
  -C "$(dirname "${SOURCE_DIR}")" "$(basename "${SOURCE_DIR}")"
printf '%s\n' "${OUTPUT}"
