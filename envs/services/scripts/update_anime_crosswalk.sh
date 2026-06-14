#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${REPO_DIR:-/srv/anime-lists}"
DATA_DIR="${DATA_DIR:-/var/lib/anime-crosswalk}"
DB_PATH="$DATA_DIR/anime_lists.sqlite"
NEXT_DB_PATH="$DATA_DIR/anime_lists.sqlite.next"
BRANCH="${BRANCH:-master}"
TOOLKIT_DIR="${TOOLKIT_DIR:-/opt/ja-media-toolkit}"

cd "$REPO_DIR"
old_sha="$(git rev-parse HEAD)"
git fetch --quiet origin "$BRANCH"
new_sha="$(git rev-parse "origin/$BRANCH")"

if [ "$old_sha" = "$new_sha" ]; then
  echo "No upstream change: $new_sha"
  exit 0
fi

git reset --hard "origin/$BRANCH"
rm -f "$NEXT_DB_PATH"

cd "$TOOLKIT_DIR/envs/services"
uv run anime-crosswalk-ingest \
  --input "$REPO_DIR/anime-list-full.json" \
  --output "$NEXT_DB_PATH" \
  --source-repo "Fribb/anime-lists" \
  --source-branch "$BRANCH" \
  --source-commit "$new_sha"
uv run anime-crosswalk-smoke "$NEXT_DB_PATH"

mv "$NEXT_DB_PATH" "$DB_PATH"
systemctl restart anime-crosswalk.service
echo "Updated anime-crosswalk from $old_sha to $new_sha"
