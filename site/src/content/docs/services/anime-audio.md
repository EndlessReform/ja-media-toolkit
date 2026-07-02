---
title: Anime Audio Service
description: Index and retrieve derived anime audio and embedded subtitles by identity.
---

The Anime Audio service indexes the `.ja-media.json` manifests produced by the
audio-library tool. Manifests remain authoritative: the SQLite index is a
rebuildable lookup cache, and Audiobookshelf remains a consumer of the same
files rather than a metadata source. Embedded text subtitles extracted during
ingest are indexed beside the audio artifacts so tools can fetch timing
references without scanning the media library.

The gateway prefix is `/api/v1/audio`.

## Configure the library

Set the host path when starting Compose:

```sh
ANIME_AUDIO_LIBRARY_PATH=/path/to/derived-anime-audio docker compose up -d --build
```

The directory is mounted read-only. The service stores only its rebuildable
SQLite index in the `anime-audio-data` volume.

Index maintenance defaults to native filesystem events with a one-second
debounce and a metadata-only fallback scan every five minutes:

```text
ANIME_AUDIO_WATCHER_ENABLED=true
ANIME_AUDIO_WATCHER_DEBOUNCE_SECONDS=1
ANIME_AUDIO_FALLBACK_SCAN_INTERVAL_SECONDS=300
```

Disable the watcher only when filesystem events are known to be unusable. Keep
the fallback scan enabled for NFS libraries because changes made by another
client may not produce events on the service host.

## API

```text
GET  /inventory
GET  /series/{anilist_id}
GET  /series/{anilist_id}/episodes
GET  /series/{anilist_id}/episodes/{episode_key}
GET  /series/{anilist_id}/episodes/{episode_key}/artifacts/{profile}
GET  /series/{anilist_id}/episodes/{episode_key}/artifacts/{profile}/content
GET  /series/{anilist_id}/episodes/{episode_key}/subtitles
GET  /series/{anilist_id}/episodes/{episode_key}/subtitles/{subtitle_id}/content
GET  /series/{anilist_id}/episodes/{episode_key}/subtitles/content?id=stream-3&id=stream-4
GET  /series/{anilist_id}/episodes/{episode_key}/subtitles/content?getall=true
POST /reconcile
GET  /healthz
GET  /metrics
```

Responses expose stable identity and measured artifact facts, never host
filesystem paths. The content endpoint uses Starlette's file response, which
supports ordinary range requests for seeking.

`GET /inventory` projects the complete index in one payload: bounded top-level
counts plus every indexed series with its episode keys and available artifact
profiles, subtitle count, and subtitle languages. Series are ordered by AniList
ID; episode keys are sorted numerically and profiles alphabetically. The
initial library is small enough that pagination is unnecessary.

Episode responses include a `subtitles` array. Subtitle IDs are stable source
stream IDs such as `stream-3`, and each subtitle row includes language, title,
codec, source stream index, size, checksum, and `content_url`. The service
advertises every extracted text subtitle, including Japanese tracks; clients
choose their own default policy.

The bulk subtitle content endpoint returns JSON records with the public
subtitle metadata and `content_base64`. Use repeated `id` query parameters to
fetch selected tracks, or `getall=true` to fetch every subtitle for the
episode.

```sh
ROOT_URL=http://localhost:8080

curl -fsS "$ROOT_URL/api/v1/audio/inventory" | jq .
curl -fsS "$ROOT_URL/api/v1/audio/series/154587" | jq .
curl -fsS "$ROOT_URL/api/v1/audio/series/154587/episodes" | jq .
curl -fsS "$ROOT_URL/api/v1/audio/series/154587/episodes/1/subtitles" | jq .
curl -fsS "$ROOT_URL/api/v1/audio/series/154587/episodes/1/subtitles/content?getall=true" | jq .
curl -fsS -X POST "$ROOT_URL/api/v1/audio/reconcile" | jq .
```

## Python SDK

Configure `[services].root_url`, then use the core client:

```python
from ja_media_core import HttpAnimeAudioClient

client = HttpAnimeAudioClient()
inventory = client.inventory()
for series in inventory.series:
    print(series.anilist_id, series.episode_keys, series.artifact_profiles)

artifact = client.artifact(154587, "1")
audio = client.content(154587, "1")
subtitles = client.subtitles(154587, "1")
subtitle_payloads = client.subtitle_contents(154587, "1", get_all=True)

print(artifact.filename, artifact.duration_ms, len(audio))
print([subtitle.language for subtitle in subtitles])
print([len(payload.content) for payload in subtitle_payloads])
```

`ANIME_AUDIO_BASE_URL` is available as a narrow direct-service override.
Clients ignore ambient proxy settings because this is a first-party LAN API.

## Reconciliation and health

Startup and `POST /reconcile` scan immediate child manifests. A complete scan
atomically replaces the index, so deleted manifests remove stale rows.
Malformed manifests, path escapes, and missing artifacts are omitted and
reported as reconciliation errors.

During normal operation, creation, replacement, movement, and deletion of an
immediate-child `.ja-media.json` refreshes only that series. Event bursts from
atomic publication are debounced. The fallback scan compares each manifest's
relative identity, modification time, and size with SQLite; it parses only new
or changed manifests and removes rows only after a complete directory scan.
It does not read, hash, or probe unchanged audio artifacts.
Subtitle artifacts are validated when manifests are indexed. A missing
subtitle degrades the whole manifest in the same way as a missing audio
artifact, because the manifest is the authoritative contract.

`GET /healthz` returns:

- `ok` when the index is ready and the last scan had no errors;
- `degraded` when usable indexed data remains but the scan found errors or the
  library later became unavailable;
- HTTP 503 with `unavailable` when no usable index can be served.

The response includes bounded watcher state, the last successful incremental
scan, scan and refresh failure counters, index counts, and reconciliation
timestamps, but no configured paths.

## Metrics

`GET /metrics` exports:

```text
anime_audio_index_ready
anime_audio_series_total
anime_audio_artifacts_total
anime_audio_subtitles_total
anime_audio_reconciliation_errors
anime_audio_last_reconciliation_timestamp_seconds
anime_audio_watcher_running
anime_audio_fallback_scan_worker_running
anime_audio_last_incremental_scan_timestamp_seconds
anime_audio_incremental_scan_failures_total
anime_audio_manifest_refresh_failures_total
```

The endpoint is registered in the deployment's Prometheus HTTP-SD document.
