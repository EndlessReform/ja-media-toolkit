#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env"

if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
fi

SERVER_PORT="${SERVER_PORT:-8000}"
BASE_URL="${BASE_URL:-http://127.0.0.1:${SERVER_PORT}}"

curl --fail --silent --show-error "${BASE_URL}/health" >/dev/null
curl --fail --silent --show-error "${BASE_URL}/v1/models"
printf '\n'
