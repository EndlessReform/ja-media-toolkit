#!/usr/bin/env sh
set -eu

if [ -z "${ANIME_CROSSWALK_BASE_URL:-}" ]; then
  echo "ANIME_CROSSWALK_BASE_URL is not set" >&2
  exit 64
fi

if [ "$#" -lt 2 ] || [ "$#" -gt 3 ]; then
  echo "usage: resolve.sh SOURCE ID [tv|movie]" >&2
  exit 64
fi

base="${ANIME_CROSSWALK_BASE_URL%/}"
source="$1"
external_id="$2"

if [ "$#" -eq 3 ]; then
  media_kind="$3"
  curl -fsS "$base/resolve/$source/$media_kind/$external_id"
else
  curl -fsS "$base/resolve/$source/$external_id"
fi
