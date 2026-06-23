# Derived anime audio library

This document is the developer reference for the derived anime audio artifact
format. It describes the filesystem, metadata, and conversion contracts that
other tools and services may build on.

User-facing command instructions live in the docsite. Proposed service work
lives in [`docs/plans/derived-anime-audio-library.md`](../plans/derived-anime-audio-library.md).

## Filesystem contract

The destination root contains one immediate directory per AniList media ID:

```text
<destination-root>/
└── anilist-<id>/
    ├── .ja-media.json
    ├── metadata.json
    ├── cover.jpg
    ├── S01E001.m4a
    └── S01E002.m4a
```

- `anilist-<id>` is the stable series identity. The title is deliberately not
  part of the path.
- `.ja-media.json` is the authoritative repository-owned manifest.
- `metadata.json` is an Audiobookshelf projection.
- `cover.jpg` is an optional verified projection of the AniList cover.
- `S01E<nnn>.m4a` is the current filename policy for positive integer episode
  keys.
- Partial files are siblings named like `.S01E001.partial.m4a` or
  `..ja-media.json.partial`; publication uses atomic replacement.

The layout follows AniList media identity. Separate AniList records for sequel
seasons or split cours therefore produce separate directories. No franchise
season model is inferred.

## Authoritative manifest

The manifest is represented by `AnimeAudioManifest` in
`packages/core/src/ja_media_core/audio_library.py` and serialized by
`manifest_to_mapping()` in
`packages/frontend/src/ja_media_frontend/audio_library/manifest.py`.

Schema identity:

```json
{
  "schema_version": 1,
  "kind": "anime-audio-series"
}
```

The remaining shape is:

```text
series
  normalized AniList fields
  metadata_snapshot       selected source fields before normalization
  cover                   verified local cover facts, or null

profile
  name, container, codec, bitrate_bps, max_channels, sample_rate_hz

episodes[]
  episode_key
  source
    relative_path, size_bytes, mtime_ns
    global_stream_index, audio_stream_ordinal
    audio_codec, audio_language
  artifact
    relative_path, size_bytes, duration_ms
    codec, bitrate_bps, channels, sample_rate_hz, sha256
  created_at
```

Source paths are relative to the source directory supplied for the ingest.
The source fingerprint is `(relative_path, size_bytes, mtime_ns)`; hashing a
multi-gigabyte source would require another complete read. Derived artifacts
are hashed because they are much smaller.

`global_stream_index` is ffprobe's container-global stream index and is the
value passed to `ffmpeg -map 0:<index>`. `audio_stream_ordinal` is the
zero-based position among audio streams and exists for UI and diagnostics.

The schema permits string episode keys, but the current materializer accepts
only positive integers because special and fractional episodes need an
explicit collision-free filename policy.

## AniList metadata provenance

Metadata comes from the first-party AniList search service through
`HttpAniListSearchClient`; audio-library code does not call AniList directly.

```text
GET /api/v1/anilist/search
GET /api/v1/anilist/anime/{anilist_id}?fields=...
```

The client resolves the service through an explicit URL,
`ANILIST_SEARCH_BASE_URL`, or `[services].root_url` plus
`/api/v1/anilist`. The service record is a cached dataset row whose columns
retain their AniList field names and may contain CSV-shaped values.

`SELECTED_FIELDS` in
`packages/frontend/src/ja_media_frontend/audio_library/metadata.py` requests:

```text
title_english, title_native, title_romaji, title_userPreferred
description, format, status, season, seasonYear, episodes, duration
startDate_year, startDate_month, startDate_day
endDate_year, endDate_month, endDate_day
genres, source, countryOfOrigin
coverImage_extraLarge, coverImage_large, coverImage_medium, bannerImage
idMal, siteUrl, updatedAt
```

`normalize_anilist_metadata()` converts that row into
`AnimeAudioSeriesMetadata`:

- integer-valued floats become integers;
- invalid or incomplete dates become `None`;
- `genres` accepts either a JSON-encoded string or a decoded list;
- AniList description HTML is retained and a plain-text projection is derived;
- the preferred title is English, then romaji, native, user-preferred, then
  `AniList <id>`;
- cover selection prefers extra-large, large, then medium;
- the selected unnormalized values are retained in `metadata_snapshot`.

The normalized fields are conveniences, not a replacement for provenance.
Consumers needing a field not present in the manifest should query the AniList
service again by the already-established AniList ID.

## Conversion contract

`PORTABLE_AAC_V1` is the only current profile:

```text
container       m4a
codec           AAC-LC
target bitrate  128000 bps
sample rate     48000 Hz
channels        preserve mono; otherwise cap at 2
```

The application decides:

- which source files and episodes are in scope;
- which container-global audio stream to map;
- output filename and temporary-file placement;
- the profile values and metadata tags;
- whether an existing artifact matches its recorded source and profile;
- whether verification succeeded and publication may occur;
- manifest checkpointing and artifact hashing.

ffmpeg performs the media transformation. `build_ffmpeg_command()` delegates
decoding, resampling, downmixing, AAC encoding, M4A muxing, and fast-start
layout:

```text
ffmpeg -i SOURCE
  -map 0:<global-stream-index> -vn
  -ac <1-or-2> -ar 48000
  -c:a aac -b:a 128000
  -movflags +faststart
  <metadata arguments>
  TEMPORARY_OUTPUT
```

No custom DSP or audio codec implementation exists in the toolkit. The
repository logic is selection, policy, provenance, safe execution, and
verification around ffmpeg.

After ffmpeg exits, `verify_audio_artifact()` uses ffprobe to require exactly
one audio stream, AAC codec, a positive duration, a valid channel count no
greater than the profile maximum, the expected sample rate, and a nonempty
file. Only then does `materialize_episode()` atomically rename the temporary
file and return an `ArtifactRecord`.

Cover downloads follow the same pattern: size-limit the response, use ffprobe
to require one decodable image stream with positive dimensions, then publish
atomically.

## Code map

### Durable contracts

`packages/core/src/ja_media_core/audio_library.py`:

- `AudioStreamProbe`, `SourceMediaProbe`
- `AnimeAudioSeriesMetadata`
- `EpisodeMapping`, `MaterializationPlan`
- `AudioProfile`, `PORTABLE_AAC_V1`
- `CoverArtifact`, `ArtifactRecord`, `ManifestEpisode`
- `AnimeAudioManifest`

`packages/core/src/ja_media_core/media_filename.py`:

- `parse_media_filename()`
- `suggest_ordinary_episode()`

`packages/core/src/ja_media_core/anilist_search.py`:

- `AnimeMetadata`, `AniListSearchClient`
- `HttpAniListSearchClient`

### Filesystem and process adapters

`packages/frontend/src/ja_media_frontend/audio_library/discovery.py`:

- `discover_media()` discovers supported immediate children.
- `probe_media()` adapts ffprobe JSON into `SourceMediaProbe`.
- `choose_unambiguous_audio_stream()` applies language/default evidence.

`packages/frontend/src/ja_media_frontend/audio_library/metadata.py`:

- `normalize_anilist_metadata()` adapts the cached AniList row.
- `download_cover()` verifies and publishes the selected cover.

`packages/frontend/src/ja_media_frontend/audio_library/materialize.py`:

- `artifact_filename()` applies the current episode filename policy.
- `build_ffmpeg_command()` is the reproducible conversion boundary.
- `materialize_episode()` converts, verifies, and publishes.
- `verify_audio_artifact()` returns measured artifact facts.

`packages/core/src/ja_media_core/audio_manifest.py`:

- `manifest_from_mapping()` supports schema version 1.
- `manifest_to_mapping()` defines the JSON representation.

`packages/frontend/src/ja_media_frontend/audio_library/manifest.py`:

- `load_manifest()` reads JSON through the shared core decoder.
- `write_manifest_atomic()` and `write_metadata_atomic()` publish JSON.
- `project_audiobookshelf_metadata()` creates the consumer projection.

Interactive planning currently composes these adapters in `wizard.py`.
Noninteractive consumers should reuse the contracts and adapters, not invoke
the CLI or reproduce the manifest schema independently. The reusable adapters
should move out of frontend when the indexed service is implemented.
