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
: "${KITSUNEKKO_SUBTITLES_UPSTREAM_REPO_URL:=https://github.com/Ajatt-Tools/kitsunekko-mirror.git}"
: "${KITSUNEKKO_SUBTITLES_UPSTREAM_REPO_NAME:=Ajatt-Tools/kitsunekko-mirror}"
: "${KITSUNEKKO_SUBTITLES_UPSTREAM_BRANCH:=main}"
: "${KITSUNEKKO_SUBTITLES_UPDATE_INTERVAL_SECONDS:=3600}"
: "${KITSUNEKKO_SUBTITLES_UPDATE_ON_START:=1}"

export KITSUNEKKO_SUBTITLES_DB_PATH
export KITSUNEKKO_SUBTITLES_CROSSWALK_DB_PATH
export KITSUNEKKO_SUBTITLES_MIRROR_DIR

kitsunekko_subtitles_commit_path="$KITSUNEKKO_SUBTITLES_DATA_DIR/mirror_commit"

kitsunekko_subtitles_sync_repo() {
  mkdir -p "$KITSUNEKKO_SUBTITLES_DATA_DIR"

  started_at="$(date +%s)"
  if [ -d "$KITSUNEKKO_SUBTITLES_MIRROR_DIR/.git" ]; then
    echo "kitsunekko-subtitles: fetching $KITSUNEKKO_SUBTITLES_UPSTREAM_REPO_NAME $KITSUNEKKO_SUBTITLES_UPSTREAM_BRANCH" >&2
    if ! git -C "$KITSUNEKKO_SUBTITLES_MIRROR_DIR" fetch --quiet --depth 1 origin "$KITSUNEKKO_SUBTITLES_UPSTREAM_BRANCH"; then
      echo "kitsunekko-subtitles: git fetch failed" >&2
      return 70
    fi
    if ! git -C "$KITSUNEKKO_SUBTITLES_MIRROR_DIR" reset --quiet --hard FETCH_HEAD; then
      echo "kitsunekko-subtitles: git reset failed" >&2
      return 70
    fi
  else
    echo "kitsunekko-subtitles: cloning $KITSUNEKKO_SUBTITLES_UPSTREAM_REPO_NAME $KITSUNEKKO_SUBTITLES_UPSTREAM_BRANCH into $KITSUNEKKO_SUBTITLES_MIRROR_DIR; first clone can take several minutes" >&2
    rm -rf "$KITSUNEKKO_SUBTITLES_MIRROR_DIR"
    if ! git clone \
      --quiet \
      --depth 1 \
      --branch "$KITSUNEKKO_SUBTITLES_UPSTREAM_BRANCH" \
      --single-branch \
      "$KITSUNEKKO_SUBTITLES_UPSTREAM_REPO_URL" \
      "$KITSUNEKKO_SUBTITLES_MIRROR_DIR"; then
      echo "kitsunekko-subtitles: git clone failed" >&2
      return 70
    fi
  fi
  finished_at="$(date +%s)"
  echo "kitsunekko-subtitles: mirror sync finished in $((finished_at - started_at))s" >&2

  git -C "$KITSUNEKKO_SUBTITLES_MIRROR_DIR" rev-parse HEAD
}

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

  old_commit=""
  if [ -r "$kitsunekko_subtitles_commit_path" ]; then
    old_commit="$(cat "$kitsunekko_subtitles_commit_path")"
  fi

  if ! new_commit="$(kitsunekko_subtitles_sync_repo)"; then
    if [ -s "$KITSUNEKKO_SUBTITLES_DB_PATH" ]; then
      echo "kitsunekko-subtitles: mirror sync failed; keeping existing DB" >&2
      return 0
    fi
    echo "kitsunekko-subtitles: mirror sync failed and no DB exists" >&2
    return 70
  fi

  if [ -s "$KITSUNEKKO_SUBTITLES_DB_PATH" ] && [ "$old_commit" = "$new_commit" ]; then
    if uv run --no-sync kitsunekko-subtitles-smoke "$KITSUNEKKO_SUBTITLES_DB_PATH" >/dev/null; then
      echo "kitsunekko-subtitles: DB is current at $new_commit"
      return 0
    fi
    echo "kitsunekko-subtitles: existing DB failed smoke test; rebuilding $new_commit" >&2
  fi

  mkdir -p "$(dirname "$KITSUNEKKO_SUBTITLES_DB_PATH")"
  next_db="${KITSUNEKKO_SUBTITLES_DB_PATH}.next"
  rm -f "$next_db"

  if ! uv run --no-sync kitsunekko-subtitles-ingest \
    --output "$next_db" \
    --crosswalk-db "$KITSUNEKKO_SUBTITLES_CROSSWALK_DB_PATH" \
    --mirror-dir "$KITSUNEKKO_SUBTITLES_MIRROR_DIR" \
    --mirror-repo "$KITSUNEKKO_SUBTITLES_UPSTREAM_REPO_NAME" \
    --mirror-branch "$KITSUNEKKO_SUBTITLES_UPSTREAM_BRANCH" \
    --mirror-commit "$new_commit"; then
    echo "kitsunekko-subtitles: ingest failed; not promoting DB" >&2
    rm -f "$next_db"
    return 70
  fi

  if ! uv run --no-sync kitsunekko-subtitles-smoke "$next_db"; then
    echo "kitsunekko-subtitles: smoke test failed; not promoting DB" >&2
    rm -f "$next_db"
    return 70
  fi

  mv "$next_db" "$KITSUNEKKO_SUBTITLES_DB_PATH"
  printf '%s\n' "$new_commit" > "$kitsunekko_subtitles_commit_path"
  echo "kitsunekko-subtitles: updated DB from ${old_commit:-none} to $new_commit"
  return 10
}

run_kitsunekko_subtitles_prelude() {
if [ "$KITSUNEKKO_SUBTITLES_UPDATE_ON_START" != "0" ] || [ ! -s "$KITSUNEKKO_SUBTITLES_DB_PATH" ]; then
  set +e
  kitsunekko_subtitles_update_once
  update_status="$?"
  set -e
  if [ "$update_status" -ne 0 ] && [ "$update_status" -ne 10 ]; then
    exit "$update_status"
  fi
elif [ ! -s "$KITSUNEKKO_SUBTITLES_DB_PATH" ]; then
  echo "kitsunekko-subtitles: update on start is disabled and no DB exists" >&2
  exit 64
fi

if [ "$KITSUNEKKO_SUBTITLES_UPDATE_INTERVAL_SECONDS" -gt 0 ] 2>/dev/null; then
  (
    while :; do
      sleep "$KITSUNEKKO_SUBTITLES_UPDATE_INTERVAL_SECONDS" || exit 0
      set +e
      kitsunekko_subtitles_update_once
      update_status="$?"
      set -e

      if [ "$update_status" -eq 10 ]; then
        echo "kitsunekko-subtitles: DB changed; restarting API process"
        kill -TERM 1
        exit 0
      fi
      if [ "$update_status" -ne 0 ]; then
        echo "kitsunekko-subtitles: scheduled update failed" >&2
      fi
    done
  ) &
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
