#!/usr/bin/env bash

# Source the existing data configuration without copying Bronze values into the
# aligner deployment. The caller defines REPO_ROOT before loading this file.
JA_MEDIA_DATA_CONFIG_HOST="${JA_MEDIA_DATA_CONFIG_HOST:-${REPO_ROOT}/packages/data/config.local.toml}"
if [[ ! -f "${JA_MEDIA_DATA_CONFIG_HOST}" ]]; then
  echo "data config not found: ${JA_MEDIA_DATA_CONFIG_HOST}" >&2
  return 1
fi

config_name="$(basename "${JA_MEDIA_DATA_CONFIG_HOST}")"
environment="${config_name#config.}"
environment="${environment%.toml}"
secrets_file="$(dirname "${JA_MEDIA_DATA_CONFIG_HOST}")/.env.${environment}"
if [[ -f "${secrets_file}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${secrets_file}"
  set +a
fi

: "${JA_MEDIA_BRONZE__ACCESS_KEY_ID:?missing from process or ${secrets_file}}"
: "${JA_MEDIA_BRONZE__SECRET_ACCESS_KEY:?missing from process or ${secrets_file}}"
export JA_MEDIA_DATA_CONFIG_HOST
