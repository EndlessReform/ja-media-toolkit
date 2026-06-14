#!/usr/bin/env sh
set -eu

command_name="$*"

: "${ANIME_CROSSWALK_DATA_DIR:=/var/lib/anime-crosswalk}"
: "${ANIME_CROSSWALK_DB_PATH:=/var/lib/anime-crosswalk/anime_lists.sqlite}"
: "${ANIME_CROSSWALK_REPO_DIR:=$ANIME_CROSSWALK_DATA_DIR/anime-lists}"
: "${ANIME_CROSSWALK_SOURCE_JSON_PATH:=$ANIME_CROSSWALK_REPO_DIR/anime-list-full.json}"
: "${ANIME_CROSSWALK_UPSTREAM_REPO_URL:=https://github.com/Fribb/anime-lists.git}"
: "${ANIME_CROSSWALK_UPSTREAM_REPO_NAME:=Fribb/anime-lists}"
: "${ANIME_CROSSWALK_UPSTREAM_BRANCH:=master}"
: "${ANIME_CROSSWALK_UPDATE_INTERVAL_SECONDS:=43200}"
: "${ANIME_CROSSWALK_UPDATE_ON_START:=1}"

export ANIME_CROSSWALK_DB_PATH
export ANIME_CROSSWALK_SOURCE_JSON_PATH

anime_crosswalk_commit_path="$ANIME_CROSSWALK_DATA_DIR/source_commit"

anime_crosswalk_sync_repo() {
  mkdir -p "$ANIME_CROSSWALK_DATA_DIR"

  if [ -d "$ANIME_CROSSWALK_REPO_DIR/.git" ]; then
    echo "anime-crosswalk: fetching $ANIME_CROSSWALK_UPSTREAM_REPO_NAME $ANIME_CROSSWALK_UPSTREAM_BRANCH" >&2
    git -C "$ANIME_CROSSWALK_REPO_DIR" fetch --quiet --depth 1 origin "$ANIME_CROSSWALK_UPSTREAM_BRANCH"
    git -C "$ANIME_CROSSWALK_REPO_DIR" reset --quiet --hard FETCH_HEAD
  else
    echo "anime-crosswalk: cloning $ANIME_CROSSWALK_UPSTREAM_REPO_NAME $ANIME_CROSSWALK_UPSTREAM_BRANCH into $ANIME_CROSSWALK_REPO_DIR" >&2
    rm -rf "$ANIME_CROSSWALK_REPO_DIR"
    git clone \
      --quiet \
      --depth 1 \
      --branch "$ANIME_CROSSWALK_UPSTREAM_BRANCH" \
      --single-branch \
      "$ANIME_CROSSWALK_UPSTREAM_REPO_URL" \
      "$ANIME_CROSSWALK_REPO_DIR"
  fi

  git -C "$ANIME_CROSSWALK_REPO_DIR" rev-parse HEAD
}

anime_crosswalk_update_once() {
  old_commit=""
  if [ -r "$anime_crosswalk_commit_path" ]; then
    old_commit="$(cat "$anime_crosswalk_commit_path")"
  fi

  if ! new_commit="$(anime_crosswalk_sync_repo)"; then
    if [ -s "$ANIME_CROSSWALK_DB_PATH" ]; then
      echo "anime-crosswalk: upstream sync failed; keeping existing DB" >&2
      return 0
    fi
    echo "anime-crosswalk: upstream sync failed and no DB exists" >&2
    return 70
  fi

  if [ -s "$ANIME_CROSSWALK_DB_PATH" ] && [ "$old_commit" = "$new_commit" ]; then
    echo "anime-crosswalk: DB is current at $new_commit"
    return 0
  fi

  mkdir -p "$(dirname "$ANIME_CROSSWALK_DB_PATH")"
  next_db="${ANIME_CROSSWALK_DB_PATH}.next"
  rm -f "$next_db"

  uv run --no-sync anime-crosswalk-ingest \
    --input "$ANIME_CROSSWALK_SOURCE_JSON_PATH" \
    --output "$next_db" \
    --source-repo "$ANIME_CROSSWALK_UPSTREAM_REPO_NAME" \
    --source-branch "$ANIME_CROSSWALK_UPSTREAM_BRANCH" \
    --source-commit "$new_commit"

  uv run --no-sync anime-crosswalk-smoke "$next_db"

  mv "$next_db" "$ANIME_CROSSWALK_DB_PATH"
  printf '%s\n' "$new_commit" > "$anime_crosswalk_commit_path"
  echo "anime-crosswalk: updated DB from ${old_commit:-none} to $new_commit"
  return 10
}

run_anime_crosswalk_prelude() {
if [ "$ANIME_CROSSWALK_UPDATE_ON_START" != "0" ]; then
  set +e
  anime_crosswalk_update_once
  update_status="$?"
  set -e
  if [ "$update_status" -ne 0 ] && [ "$update_status" -ne 10 ]; then
    exit "$update_status"
  fi
elif [ ! -s "$ANIME_CROSSWALK_DB_PATH" ]; then
  echo "anime-crosswalk: update on start is disabled and no DB exists" >&2
  exit 64
fi

if [ "$ANIME_CROSSWALK_UPDATE_INTERVAL_SECONDS" -gt 0 ] 2>/dev/null; then
  (
    while :; do
      sleep "$ANIME_CROSSWALK_UPDATE_INTERVAL_SECONDS" || exit 0
      set +e
      anime_crosswalk_update_once
      update_status="$?"
      set -e

      if [ "$update_status" -eq 10 ]; then
        echo "anime-crosswalk: DB changed; restarting API process"
        kill -TERM 1
        exit 0
      fi
      if [ "$update_status" -ne 0 ]; then
        echo "anime-crosswalk: scheduled update failed" >&2
      fi
    done
  ) &
fi
}

: "${KITSUNEKKO_SUBTITLES_DATA_DIR:=/var/lib/kitsunekko-subtitles}"
: "${KITSUNEKKO_SUBTITLES_DB_PATH:=/var/lib/kitsunekko-subtitles/kitsunekko_subtitles.sqlite}"
: "${KITSUNEKKO_SUBTITLES_CROSSWALK_DB_PATH:=/var/lib/anime-crosswalk-ro/anime_lists.sqlite}"
: "${KITSUNEKKO_SUBTITLES_MIRROR_DIR:=$KITSUNEKKO_SUBTITLES_DATA_DIR/kitsunekko-mirror}"
: "${KITSUNEKKO_SUBTITLES_UPSTREAM_REPO_URL:=git@github.com:Ajatt-Tools/kitsunekko-mirror.git}"
: "${KITSUNEKKO_SUBTITLES_UPSTREAM_REPO_NAME:=Ajatt-Tools/kitsunekko-mirror}"
: "${KITSUNEKKO_SUBTITLES_UPSTREAM_BRANCH:=main}"
: "${KITSUNEKKO_SUBTITLES_UPDATE_ON_START:=1}"

export KITSUNEKKO_SUBTITLES_DB_PATH
export KITSUNEKKO_SUBTITLES_CROSSWALK_DB_PATH

kitsunekko_subtitles_update_once() {
  mkdir -p "$KITSUNEKKO_SUBTITLES_DATA_DIR"

  if [ ! -s "$KITSUNEKKO_SUBTITLES_CROSSWALK_DB_PATH" ]; then
    if [ -s "$KITSUNEKKO_SUBTITLES_DB_PATH" ]; then
      echo "kitsunekko-subtitles: crosswalk DB unavailable; keeping existing DB" >&2
      return 0
    fi
    echo "kitsunekko-subtitles: crosswalk DB unavailable and no DB exists: $KITSUNEKKO_SUBTITLES_CROSSWALK_DB_PATH" >&2
    return 70
  fi

  echo "kitsunekko-subtitles: Kitsunekko git sync is not enabled yet; building crosswalk-only DB"

  mkdir -p "$(dirname "$KITSUNEKKO_SUBTITLES_DB_PATH")"
  next_db="${KITSUNEKKO_SUBTITLES_DB_PATH}.next"
  rm -f "$next_db"

  uv run --no-sync kitsunekko-subtitles-ingest \
    --output "$next_db" \
    --crosswalk-db "$KITSUNEKKO_SUBTITLES_CROSSWALK_DB_PATH" \
    --mirror-repo "$KITSUNEKKO_SUBTITLES_UPSTREAM_REPO_NAME" \
    --mirror-branch "$KITSUNEKKO_SUBTITLES_UPSTREAM_BRANCH" \
    --mirror-commit "not-synced"

  uv run --no-sync kitsunekko-subtitles-smoke "$next_db"

  mv "$next_db" "$KITSUNEKKO_SUBTITLES_DB_PATH"
  echo "kitsunekko-subtitles: promoted crosswalk-only DB from $KITSUNEKKO_SUBTITLES_CROSSWALK_DB_PATH"
  return 0
}

run_kitsunekko_subtitles_prelude() {
if [ "$KITSUNEKKO_SUBTITLES_UPDATE_ON_START" != "0" ] || [ ! -s "$KITSUNEKKO_SUBTITLES_DB_PATH" ]; then
  kitsunekko_subtitles_update_once
fi
}

case "$command_name" in
  *anime-crosswalk*)
    run_anime_crosswalk_prelude
    ;;
  *kitsunekko-subtitles*)
    run_kitsunekko_subtitles_prelude
    ;;
esac

exec "$@"
