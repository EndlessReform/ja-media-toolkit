#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# shellcheck source=load-data-config.sh
source "${ROOT_DIR}/scripts/load-data-config.sh"

cd "${ROOT_DIR}"
docker compose build --pull adapter
docker compose up -d --no-deps adapter
