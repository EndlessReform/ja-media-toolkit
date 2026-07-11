# Phase 0–1: bronze contract and access

Status: proposed. No implementation is authorized by this document.

## Goal

Make committed bronze audio and subtitle sidecars discoverable and streamable
without inventing episode identity or exposing S3 credentials to callers.

This phase extends the existing anime-audio service. It does not depend on an
orchestrator choice. Shared bronze records and S3 helpers may live in focused
modules, but the public API stays concrete.

## What bronze promises

Bronze is captured evidence. A committed capture says:

- which series namespace and ID were supplied to ingest;
- which audio stream and text subtitle streams were extracted;
- what the container declared about codec, language, title, and default flags;
- where the committed objects live relative to the capture prefix;
- when capture completed and which manifest schema wrote it.

Bronze does not promise an episode number, measured language, correct timing,
clean cues, normalized loudness, or suitability for Audiobookshelf or ASR.

The current layout remains valid:

```text
audio/anime/bronze/{anilist_id}/
  {stem}.{audio_extension}
  subs/{stem}/{subtitle_filename}.srt
  metadata/{stem}.json
```

The metadata JSON is written last and is the commit marker. Readers ignore
orphan audio and subtitle objects. Existing objects are not rearranged.

## Minimal manifest revision

New writers add durable repository IDs and explicit names for hints. A reader
supports the existing schema and this v2 shape:

```json
{
  "schema_version": 2,
  "kind": "anime-bronze-capture",
  "capture_id": "01J...",
  "series": {"namespace": "anilist", "id": "15451"},
  "source_hint": "episode-file.mkv",
  "stem": "episode-file",
  "captured_at": "2026-07-09T00:00:00Z",
  "audio": {
    "key": "episode-file.ac3",
    "stream_index": 1,
    "codec": "ac3",
    "declared_language": "jpn",
    "title_hint": null,
    "is_default": true
  },
  "subtitles": [
    {
      "track_id": "01J...",
      "key": "subs/episode-file/stream_3.srt",
      "stream_index": 3,
      "source_codec": "subrip",
      "stored_format": "srt",
      "declared_language": "eng",
      "title_hint": null,
      "is_default": true
    }
  ]
}
```

`capture_id` and `track_id` are lookup identities, not semantic media IDs.
Legacy IDs should be deterministic hashes of bucket plus manifest key; that
keeps the index rebuildable without rewriting old objects.

The stored subtitle format is named separately from the source codec because
ffmpeg may have converted an embedded text stream to SRT during capture. That
conversion is part of capture provenance, not evidence that the cues are clean.

## Shared code boundary

Add a small `ja_media_core.bronze` package containing frozen records, manifest
parsing, validation, and protocols. Keep network clients out of the records.

Suggested modules:

```text
packages/core/src/ja_media_core/bronze/
  models.py       # BronzeCapture, BronzeAudio, BronzeSubtitleTrack
  manifest.py     # v1/v2 parsing and deterministic legacy IDs
  protocol.py     # listing and byte-read protocols
```

The S3 implementation belongs in `envs/services` because it owns credentials,
pagination, retries, Range reads, and the rebuildable SQLite index:

```text
envs/services/src/ja_media_services/anime_audio/
  bronze_index.py
  bronze_s3.py
  capture_api.py
```

The local ingest writer can use a separate S3 adapter in the frontend package
or a later write-focused core module. Do not make the read-only service
credentials capable of writing.

The same parser and records later feed orchestration source partitions. Dagster
or Prefect must adapt these domain contracts rather than introduce a second
interpretation of bronze manifests.

## API shape

Add capture-oriented routes beneath the existing `/api/v1/audio` gateway:

```text
GET /captures?series_namespace=anilist&series_id=15451&cursor=&limit=100
GET /captures/{capture_id}
GET /captures/{capture_id}/audio/content
GET /captures/{capture_id}/subtitle-tracks
GET /captures/{capture_id}/subtitle-tracks/{track_id}/content
POST /admin/reconcile/bronze
```

The audio and subtitle content endpoints support HTTP Range requests and a
useful `Content-Type`. They proxy S3 bytes in Phase 1. They never return bucket
names, object keys, credentials, or machine source paths in ordinary responses.

This is the principled way to serve bronze audio. Do not insert it into:

```text
/series/{anilist_id}/episodes/{episode_key}/artifacts/{profile}
```

That endpoint promises an episode mapping and a derived profile. Bronze often
has neither. S3-backed silver outputs will join that endpoint in Phase 3.

## Indexing and failure behavior

Reconciliation lists only `metadata/*.json`, parses supported manifests, and
checks that referenced objects exist. It does not run ffprobe, filename
parsing, language identification, or subtitle parsing.

Bootstrap performs one paginated scan of commit markers. Normal new ingest
should notify the catalog/orchestration adapter after committing a manifest.
An incremental cursor/ETag scan remains a low-frequency repair path. No CLI or
ordinary API request rescans the corpus.

Store ETag/version and last-modified values as scan tokens in SQLite, not as
public integrity claims. A service-computed hash only proves later bytes match
the bytes it observed; do not require one until a silver or gold consumer needs
content identity.

Health reports the filesystem-derived and bronze providers separately. A prior
usable index plus a failed S3 scan is `degraded`; no usable bronze index is
`unavailable` only for bronze-specific operations, not necessarily for the
legacy filesystem provider.

Metrics include low-cardinality counts and timestamps for committed captures,
invalid manifests, missing referenced objects, last successful scan,
consecutive failures, and scan duration.

## Phase 0 fixtures

Before implementation, save sanitized manifest fixtures for:

- one audio plus one subtitle;
- one audio plus multiple subtitles;
- one audio plus no subtitles;
- a missing referenced object;
- malformed and unsupported manifests.

Include the observed AC3/OGG/FLAC/AAC/MP3 variety in parsing tests. Streaming
tests need not decode every codec; they must preserve media type, length, and
byte ranges.

## Done when

- v1 and v2 fixtures produce stable typed records and IDs;
- pagination cannot skip or duplicate captures across a stable index;
- missing objects never appear as healthy committed captures;
- Range reads work through the core HTTP client and Caddy route;
- the old filesystem inventory and content endpoints are unchanged;
- a capture can be represented as an external/source partition without changing
  its bronze record;
- service health, metrics, Compose settings, monitoring discovery, and docsite
  configuration cover the bronze provider;
- no hand-written file crosses 500 lines or grows past 300 without a coherent
  extraction.
