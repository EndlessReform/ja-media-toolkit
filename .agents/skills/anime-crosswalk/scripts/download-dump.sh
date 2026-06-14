#!/usr/bin/env sh
set -eu

if [ -z "${ANIME_CROSSWALK_BASE_URL:-}" ]; then
  echo "ANIME_CROSSWALK_BASE_URL is not set" >&2
  exit 64
fi

if [ "$#" -ne 1 ]; then
  echo "usage: download-dump.sh OUTPUT_JSON_PATH" >&2
  exit 64
fi

base="${ANIME_CROSSWALK_BASE_URL%/}"
output_path="$1"

curl -fsSL --compressed "$base/data/anime-list-full.json" -o "$output_path"
