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
HEALTH_RETRIES="${HEALTH_RETRIES:-30}"

if ! health_response="$(curl \
  --fail \
  --silent \
  --connect-timeout 2 \
  --max-time 5 \
  --retry "${HEALTH_RETRIES}" \
  --retry-delay 1 \
  --retry-all-errors \
  "${BASE_URL}/healthz" 2>/dev/null)"; then
  printf 'Health check did not succeed after %s retries: %s/healthz\n' \
    "${HEALTH_RETRIES}" "${BASE_URL}" >&2
  exit 1
fi
printf '%s\n' "${health_response}"
