# Embedded Subtitle Extraction During Audio-Library Ingest

Extract one embedded subtitle track per source episode during the existing
`audio-library ingest` scan, store it beside the derived audio artifact in a
location Audiobookshelf will not surface as a playback subtitle, and expose it
over the anime-audio API so downstream tools (subsync, ffsubsync/alass
first-pass gating) can fetch it by identity.

## Motivation

Source MKV/MP4 files often carry non-Japanese subtitle tracks (EN, DE, etc.).
Even though these are the wrong language for a Japanese learner, they are
useful in two ways:

1. **Secondary subtitles** for cross-referencing during immersion.
2. **Alignment reference** for gating Kitsunekko JA subtitle candidates: a
   rough ffsubsync/alass pass against the audio can use the embedded sub's
   cue structure as a heuristic to reject or rank JA candidates before the
   expensive Qwen3 forced-alignment step.

The critical constraint is that the ingest wizard already runs one `ffprobe`
per source file in a strictly sequential scan (`discovery.py::probe_media`).
Adding subtitle extraction must **not** introduce a second pass over the
source directory.

## Non-Goals

- No JA subtitle extraction from source media (Kitsunekko remains the source
  for JA subs; embedded JA CC tracks are out of scope for now).
- No image-based subtitle (PGS, VobSub) extraction — only text-based codecs
  (ASS, SRT, WebVTT, `mov_text`) that ffmpeg can convert to SRT.
- No subtitle timing correction or alignment in this design — the extracted
  SRT is a verbatim dump of the embedded track with no offset adjustment.
- No multi-subtitle support — exactly one subtitle per episode, selected by
  language preference.
- No Audiobookshelf integration — ABS must not surface these as playback
  subtitle tracks.

## Architecture Overview

The change spans three layers:

```
packages/core (contracts)     ← new data types, manifest fields
packages/frontend (ingest)     ← ffprobe extension, subtitle selection, ffmpeg extraction
envs/services (API)           ← SQLite schema, new endpoints
```

Each layer changes independently, with the manifest (`.ja-media.json`) as the
durable contract between the ingest tool and the service.

## (a) ffprobe and ffmpeg Implementation

### Extend the existing probe — no second scan

`discovery.py::probe_media` currently runs ffprobe with `-select_streams a`,
which suppresses subtitle streams entirely. The fix is to **drop the
`-select_streams` filter** so ffprobe returns all streams in one call, then
partition into audio and subtitle streams in Python:

```python
# discovery.py — proposed change to probe_media
command = [
    "ffprobe",
    "-v", "error",
    # no -select_streams: get all streams in one pass
    "-show_entries",
    (
        "format=duration:"
        "stream=index,codec_type,codec_name,channels,sample_rate:"
        "stream_tags=language,title:"
        "stream_disposition=default"
    ),
    "-of", "json",
    str(path),
]
```

The cost difference is negligible: ffprobe reads the same container headers
regardless of the stream filter. The JSON payload is slightly larger
(subtitle stream metadata is a few hundred bytes each), but no additional
process is spawned and the sequential scan pattern is unchanged.

### New core contract: `SubtitleStreamProbe`

Add to `packages/core/src/ja_media_core/audio_library.py`:

```python
@dataclass(frozen=True)
class SubtitleStreamProbe:
    """One subtitle stream reported by ffprobe."""

    global_index: int
    subtitle_ordinal: int
    codec: str
    language: str | None
    title: str | None
    default: bool

    @property
    def is_text_based(self) -> bool:
        """Whether ffmpeg can convert this stream to SRT."""
        return self.codec.casefold() in _TEXT_SUBTITLE_CODECS

_TEXT_SUBTITLE_CODECS = frozenset({
    "ass", "ssa", "subrip", "srt", "webvtt", "vtt", "mov_text", "utf8",
})
```

### Extend `SourceMediaProbe`

```python
@dataclass(frozen=True)
class SourceMediaProbe:
    """Stable source fingerprint and stream inventory."""

    path: Path
    duration_ms: int
    size_bytes: int
    mtime_ns: int
    audio_streams: tuple[AudioStreamProbe, ...]
    subtitle_streams: tuple[SubtitleStreamProbe, ...] = ()
```

The default empty tuple keeps existing callers and tests working without
modification — any code that constructs `SourceMediaProbe` without
`subtitle_streams` still compiles.

### Extend `EpisodeMapping`

```python
@dataclass(frozen=True)
class EpisodeMapping:
    """One confirmed source episode and selected streams."""

    episode_key: str
    source: SourceMediaProbe
    stream: AudioStreamProbe
    subtitle_stream: SubtitleStreamProbe | None = None
    ja_subtitle_stream: SubtitleStreamProbe | None = None
```

`subtitle_stream` is the non-JA reference sub (EN/DE/any) destined for
`_subs/`. `ja_subtitle_stream` is the rare embedded JA sub destined for a
top-level sidecar (see [JA Sidecar](#ja-sidecar) below).

### Subtitle stream selection

Two independent selections happen over the already-probed streams, each
returning at most one stream:

**JA sub selection** — extract from the source file if present, written as a
top-level sidecar (see [JA Sidecar](#ja-sidecar)):

```python
def choose_ja_subtitle_stream(
    probe: SourceMediaProbe,
    *,
    ja_languages: tuple[str, ...] = ("jpn", "ja"),
) -> SubtitleStreamProbe | None:
    """Select one text-based JA subtitle if present."""
    text_streams = tuple(s for s in probe.subtitle_streams if s.is_text_based)
    ja_matches = tuple(
        s for s in text_streams
        if (s.language or "").casefold() in ja_languages
    )
    if not ja_matches:
        return None
    if len(ja_matches) == 1:
        return ja_matches[0]
    defaults = tuple(s for s in ja_matches if s.default)
    return defaults[0] if len(defaults) == 1 else ja_matches[0]
```

**Reference sub selection** — the non-JA sub for alignment gating:

```python
def choose_subtitle_stream(
    probe: SourceMediaProbe,
    *,
    preferred_languages: tuple[str, ...] = ("eng", "en", "deu", "de", "ger"),
) -> SubtitleStreamProbe | None:
    """Select one text-based non-JA subtitle by preference; fall back to any."""

    text_streams = tuple(s for s in probe.subtitle_streams if s.is_text_based)
    if not text_streams:
        return None
    for lang in preferred_languages:
        matches = tuple(
            s for s in text_streams
            if (s.language or "").casefold() == lang
        )
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            defaults = tuple(s for s in matches if s.default)
            if len(defaults) == 1:
                return defaults[0]
            return matches[0]  # first by ffprobe index order
    return text_streams[0]  # whatever the file has
```

Both selections run during `build_ingest_plan` alongside `_resolve_stream` —
zero additional ffprobe invocations, just filters over the already-probed
`SourceMediaProbe.subtitle_streams`.

### ffmpeg subtitle extraction

Add to `materialize.py`:

```python
def subtitle_filename(episode_key: str, language: str | None) -> str:
    """Canonical filename for one extracted subtitle."""
    lang = (language or "und")[:3].lower()
    return f"S01E{int(episode_key):03d}.{lang}.srt"


def build_subtitle_extraction_command(
    mapping: EpisodeMapping,
    destination: Path,
) -> list[str]:
    """Build an ffmpeg argument vector to extract one subtitle as SRT."""
    stream = mapping.subtitle_stream
    assert stream is not None
    return [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i", str(mapping.source_path),
        "-map", f"0:{stream.global_index}",
        "-c:s", "subrip",
        str(destination),
    ]


def materialize_subtitle(
    mapping: EpisodeMapping,
    destination: Path,
) -> SubtitleArtifactRecord:
    """Extract one subtitle stream to SRT, verify, and atomically publish."""
    temporary = destination.with_name(
        f".{destination.stem}.partial{destination.suffix}"
    )
    temporary.unlink(missing_ok=True)
    try:
        run_process(
            build_subtitle_extraction_command(mapping, temporary),
            check=True,
        )
        _verify_subtitle(temporary)
        os.replace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return SubtitleArtifactRecord(
        relative_path=str(destination.name),
        size_bytes=destination.stat().st_size,
        language=mapping.subtitle_stream.language,
        source_stream_index=mapping.subtitle_stream.global_index,
        sha256=_sha256(destination),
    )
```

Verification uses `ja_media_core.transcripts.read_subtitle` to confirm the
extracted SRT is parseable and non-empty:

```python
def _verify_subtitle(path: Path) -> None:
    if not path.is_file() or path.stat().st_size <= 0:
        raise ValueError(f"subtitle artifact is empty or missing: {path}")
    cues = read_subtitle(path)
    if not cues:
        raise ValueError(f"subtitle artifact has no cues: {path}")
```

### Integration into `execute_ingest_plan`

Both subtitle extractions happen inside the existing per-episode loop in
`wizard.py`, immediately after the audio artifact is materialized and before
the manifest update. A subtitle failure is **non-fatal** — it does not block
the audio artifact or fail the episode. The episode record is written with
the failing slot set to `None`.

```python
# wizard.py — inside the per-episode loop, after materialize_episode:

# --- JA sidecar (top-level, for ABS + subsync) ---
ja_subtitle_record = None
if mapping.ja_subtitle_stream is not None:
    ja_filename = ja_sidecar_filename(mapping.episode_key)
    ja_destination = series_dir / ja_filename
    try:
        if not ja_destination.exists() or replace_existing:
            ja_subtitle_record = materialize_subtitle(
                mapping, ja_destination, stream=mapping.ja_subtitle_stream
            )
        elif resume:
            _verify_subtitle(ja_destination)
            ja_subtitle_record = SubtitleArtifactRecord(
                relative_path=ja_filename,
                size_bytes=ja_destination.stat().st_size,
                language=mapping.ja_subtitle_stream.language,
                source_stream_index=mapping.ja_subtitle_stream.global_index,
                sha256=_sha256(ja_destination),
            )
    except Exception as error:
        notice(f"JA subtitle extraction failed for {filename}: {error}")

# --- Reference sub (in _subs/, for alignment gating) ---
subtitle_record = None
if mapping.subtitle_stream is not None:
    sub_dir = series_dir / "_subs"
    sub_dir.mkdir(parents=True, exist_ok=True)
    sub_filename = subtitle_filename(
        mapping.episode_key, mapping.subtitle_stream.language
    )
    sub_destination = sub_dir / sub_filename
    try:
        if not sub_destination.exists() or replace_existing:
            subtitle_record = materialize_subtitle(mapping, sub_destination)
            subtitle_record = replace(
                subtitle_record,
                relative_path=f"_subs/{sub_filename}",
            )
        elif resume:
            # verify existing subtitle is parseable
            _verify_subtitle(sub_destination)
            subtitle_record = SubtitleArtifactRecord(
                relative_path=f"_subs/{sub_filename}",
                size_bytes=sub_destination.stat().st_size,
                language=mapping.subtitle_stream.language,
                source_stream_index=mapping.subtitle_stream.global_index,
                sha256=_sha256(sub_destination),
            )
    except Exception as error:
        notice(f"Subtitle extraction failed for {filename}: {error}")
```

The `materialize_subtitle` function takes an optional `stream` parameter so
it can be reused for both the JA sidecar and the reference sub, rather than
reading from `mapping.subtitle_stream` exclusively.

## (b) File Placement — Audiobookshelf Immunity

### Directory layout

```
<destination_root>/
└── anilist-<anilist_id>/
    ├── .ja-media.json
    ├── metadata.json
    ├── cover.jpg
    ├── S01E001.m4a
    ├── S01E001.ja.srt              ← JA sidecar (if embedded JA sub existed)
    ├── S01E002.m4a                  (no JA sub in source → no sidecar)
    ├── S01E003.m4a
    ├── S01E003.ja.srt
    └── _subs/                      ← ABS does not scan here
        ├── S01E001.eng.srt
        ├── S01E002.eng.srt
        └── S01E003.deu.srt
```

### Why ABS ignores `_subs/`

Audiobookshelf discovers subtitle sidecars by matching `.srt`/`.vtt` files
whose **stem matches the audio file's stem in the same directory**. A file
in a subdirectory is never treated as a sidecar. The `_subs/` subdirectory
is invisible to ABS's subtitle matcher:

- **Not a sidecar match**: `S01E001.eng.srt` lives in `_subs/`, not next to
  `S01E001.m4a`, so ABS does not associate it with the audio file.
- **Not scanned as media**: `.srt` is not a supported audio extension, so
  ABS does not treat it as a library item.
- **Convention**: the `_` prefix sorts the directory first and signals
  "tooling-owned, not user-facing" to human readers.

### JA sidecar

When the source file contains a text-based JA subtitle stream (a vanishingly
rare but valuable case), it is extracted as a **top-level sidecar** named
`S01E001.ja.srt` directly beside the audio artifact. This is intentional:

- **ABS surfaces it**: the `.ja.srt` stem shares the audio file's `S01E001`
  prefix, so ABS associates it as a subtitle track during playback. The
  learner gets JA subtitles in their immersion player with no extra setup.
- **Subsync can use it**: the existing `discover_subtitle_file` sidecar
  discovery in `subsync/utils.py` already finds `<stem>.srt` and
  `<stem>.*.srt` files alongside media — the JA sidecar is auto-discovered
  as a candidate track.
- **No `_subs/` isolation**: unlike the EN/DE reference subs, the JA sidecar
  is *meant* to be seen by ABS and the learner.

The JA sidecar filename uses the `.ja.` infix rather than a bare `.srt`
to avoid clobbering a manually-promoted subsync output (which would be
written as `S01E001.srt` by `promote_subtitle` in `subsync/service.py`).

### Manifest relative paths

The manifest records subtitle artifacts with a `relative_path` that includes
the `_subs/` prefix (e.g. `_subs/S01E001.eng.srt`). The service's existing
`artifact_path` resolver already permits subdirectories within the series
root — it only rejects absolute paths and path traversal. No change to the
path-escape guard is needed.

## Manifest Schema Changes

### New core contract: `SubtitleArtifactRecord`

Add to `packages/core/src/ja_media_core/audio_library.py`:

```python
@dataclass(frozen=True)
class SubtitleArtifactRecord:
    """One verified extracted subtitle artifact."""

    relative_path: str
    size_bytes: int
    language: str | None
    source_stream_index: int
    sha256: str | None = None
```

### Extend `ManifestEpisode`

```python
@dataclass(frozen=True)
class ManifestEpisode:
    """One completed episode entry in the canonical manifest."""

    episode_key: str
    source_relative_path: str
    source_size_bytes: int
    source_mtime_ns: int
    global_stream_index: int
    audio_stream_ordinal: int
    audio_codec: str
    audio_language: str | None
    artifact: ArtifactRecord
    subtitle: SubtitleArtifactRecord | None = None    # ← new, optional
    created_at: str
```

The field defaults to `None`, so existing manifests without subtitles
continue to parse correctly. **No schema_version bump is needed** — the
service's `manifest_from_mapping` already handles optional fields (the
`cover` field follows the same pattern).

### Serialization (`audio_manifest.py`)

Update `_episode_to_mapping` to include the optional `subtitle` key:

```python
def _episode_to_mapping(episode: ManifestEpisode) -> dict[str, object]:
    result = {
        "episode_key": episode.episode_key,
        "source": { ... },
        "artifact": asdict(episode.artifact),
        "created_at": episode.created_at,
    }
    if episode.subtitle is not None:
        result["subtitle"] = asdict(episode.subtitle)
    return result
```

Update `_episode_from_mapping` to extract it:

```python
subtitle_payload = payload.get("subtitle")
subtitle = (
    SubtitleArtifactRecord(**dict(subtitle_payload))
    if isinstance(subtitle_payload, Mapping)
    else None
)
```

### On-disk manifest shape

```json
{
  "schema_version": 1,
  "kind": "anime-audio-series",
  "series": { ... },
  "profile": { ... },
  "episodes": [
    {
      "episode_key": "1",
      "source": { ... },
      "artifact": { ... },
      "subtitle": {
        "relative_path": "_subs/S01E001.eng.srt",
        "size_bytes": 28471,
        "language": "eng",
        "source_stream_index": 3,
        "sha256": "a1b2c3..."
      },
      "created_at": "2026-07-02T12:00:00Z"
    }
  ]
}
```

Episodes without subtitles simply omit the `subtitle` key.

## (c) API Exposure

### SQLite schema

Add a new `subtitle` table rather than overloading the `artifact` table
(which has audio-specific columns like `codec`, `channels`, `sample_rate_hz`
that are meaningless for subtitles):

```sql
CREATE TABLE IF NOT EXISTS subtitle (
  anilist_id INTEGER NOT NULL REFERENCES series(anilist_id) ON DELETE CASCADE,
  episode_key TEXT NOT NULL,
  language TEXT,
  relative_path TEXT NOT NULL,
  size_bytes INTEGER NOT NULL,
  source_stream_index INTEGER NOT NULL,
  sha256 TEXT,
  created_at TEXT NOT NULL,
  PRIMARY KEY (anilist_id, episode_key)
);
```

This is a forward-compatible additive migration — the existing `initialize()`
function in `db.py` uses `CREATE TABLE IF NOT EXISTS`, so the new table
appears on next startup without touching existing data.

### Indexing

`index.py::load_manifest` (service-side `manifest.py`) extracts subtitle rows
from each manifest episode that has a `subtitle` field, alongside the
existing artifact rows. The subtitle file's existence is checked with
`artifact_path` (same path-escape guard) and `is_file()` — a missing
subtitle file degrades the series, same as a missing audio artifact.

### New API endpoints

```
GET /series/{anilist_id}/episodes/{episode_key}/subtitle
GET /series/{anilist_id}/episodes/{episode_key}/subtitle/content
```

The metadata endpoint returns:

```json
{
  "anilist_id": 154587,
  "episode_key": "1",
  "language": "eng",
  "size_bytes": 28471,
  "source_stream_index": 3,
  "sha256": "a1b2c3...",
  "content_url": "/series/154587/episodes/1/subtitle/content"
}
```

Returns **404** (not an error body with `null`) when no subtitle exists for
the episode — this lets clients distinguish "no subtitle" from "service
error" cleanly.

The content endpoint returns `FileResponse` with `media_type="application/x-subrip"`
(the IANA type for SRT) and `filename=<basename>`.

### Core SDK client

Add to `packages/core/src/ja_media_core/anime_audio.py`:

```python
@dataclass(frozen=True)
class AnimeAudioSubtitle:
    """One extracted subtitle artifact exposed by stable identity."""

    anilist_id: int
    episode_key: str
    language: str | None
    size_bytes: int
    source_stream_index: int
    sha256: str | None
    content_url: str

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> AnimeAudioSubtitle:
        return cls(
            anilist_id=int(data["anilist_id"]),
            episode_key=str(data["episode_key"]),
            language=data.get("language"),
            size_bytes=int(data["size_bytes"]),
            source_stream_index=int(data["source_stream_index"]),
            sha256=data.get("sha256"),
            content_url=str(data["content_url"]),
        )
```

Extend the `AnimeAudioClient` protocol and `HttpAnimeAudioClient`:

```python
class AnimeAudioClient(Protocol):
    ...
    def subtitle(
        self, anilist_id: int, episode_key: str
    ) -> AnimeAudioSubtitle: ...

    def subtitle_content(
        self, anilist_id: int, episode_key: str
    ) -> bytes: ...
```

### Inventory enrichment

The `/inventory` response and `AnimeAudioInventorySeries` gain an
`episode_subtitle_count` field (the number of episodes in the series that
have a subtitle artifact). This is a cheap `COUNT(*)` join and lets clients
quickly identify which series have alignment references available without
N+1 episode lookups.

## CLI

No new flags are required — subtitle extraction is **on by default** during
`audio-library ingest`. The wizard reports subtitle selection in the plan
confirmation table.

Add one opt-out flag for cases where subtitle extraction is unwanted (e.g.
known-bad source files where ffmpeg subtitle conversion would hang):

```
--no-subtitles    Skip embedded subtitle discovery and extraction.
```

When `--no-subtitles` is passed, `probe_media` still discovers subtitle
streams (the ffprobe cost is unchanged), but the wizard skips subtitle
stream selection and `execute_ingest_plan` skips extraction. A future
optimization could restore `-select_streams a` when this flag is set, but
the savings are negligible.

## Ingest-to-Service Relationship

The ingest wizard has **no direct connection to the anime-audio service**.
It writes files (audio artifacts, `_subs/`, `.ja-media.json`) directly to the
destination filesystem, which is the same filesystem the service mounts
read-only. The wizard never calls the audio API, never POSTs to `/reconcile`,
and never uploads bytes.

The service picks up changes through its **watchdog watcher**: a filesystem
event on `.ja-media.json` triggers `refresh_manifest()` for that one series
(1s debounce), atomically replacing its SQLite rows. If the watcher is
disabled or events are missed (NFS), the fallback incremental scan (every
300s by default) catches the change by comparing manifest mtime+size. The
manifest is authoritative — the service only re-indexes when the manifest
file itself changes, not when audio or subtitle artifact files change.

```
wizard writes .ja-media.json (atomically, via os.replace)
    → watchdog event fires
    → service: refresh_manifest(series)
    → SQLite index updated for that series
```

## Backfilling Existing Directories

For series already ingested before subtitle extraction existed, the existing
`--resume` flag provides a natural backfill path without re-transcoding
audio:

```sh
ja-media audio-library ingest \
  --source /original/source/dir \
  --destination /library/root \
  --resume
```

`_can_resume` (`wizard.py:268`) validates the source fingerprint
(`source_relative_path`, `source_size_bytes`, `source_mtime_ns`,
`global_stream_index`) and the existing audio artifact — if all match, audio
transcoding is **skipped**. But the manifest has no `subtitle` entry yet, so
subtitle extraction runs for every episode with an embedded text sub. The
manifest is re-atomically-written with the new `subtitle` field, the watcher
fires, and the service re-indexes.

Requirements for the backfill to work:

- The **original source directory** must still be accessible at the same path
  with unchanged files (same size and mtime). If the source has moved or been
  re-muxed, the resume check fails and the wizard would attempt to
  re-transcode audio — hitting the `FileExistsError` guard unless
  `--replace` is also passed (which would re-transcode unnecessarily).
- The wizard must be run with the **same AniList ID** that the existing
  manifest uses. The `_initial_manifest` check (`wizard.py:148`) rejects a
  manifest belonging to a different series.

If the source is no longer available, a fallback path is to manually add
subtitle records to the manifest: extract subtitles with a standalone
ffmpeg command, place them in `_subs/`, and edit `.ja-media.json` to add the
`subtitle` field per episode. The watcher will re-index on the next manifest
write. A dedicated `audio-library add-subs` subcommand could automate this
in a future phase, but it is not needed for the initial rollout as long as
the source directory is retained.

## Subsync Integration Path (Future)

This design produces the data; consuming it is a separate task. The expected
integration is:

1. The subsync TUI (`subsync/audio_source.py`) already resolves derived
   audio via `AnimeAudioClient`. Add a parallel `resolve_subsync_subtitle`
   that calls `client.subtitle(anilist_id, episode_key)` and caches the SRT
   to the same `~/.cache/ja-media-toolkit/` tree.
2. The fetched EN/DE SRT becomes a `SubtitleTrack` with a new
   `role="reference"` flag, distinct from the JA candidate tracks.
3. An ffsubsync or alass invocation pairs the derived audio with the
   reference SRT to produce a timing transform, which is then applied to
   the JA candidates as a first-pass gate before manual review.

This is **not** part of this design — it requires its own plan because it
introduces new dependencies (ffsubsync/alass) and a new alignment workflow.

## Implementation Phases

### Phase 1: Probe and contracts

- [ ] Add `SubtitleStreamProbe`, `SubtitleArtifactRecord` to core
- [ ] Extend `SourceMediaProbe` and `EpisodeMapping` with optional subtitle fields
- [ ] Extend `ManifestEpisode` with optional `subtitle` field
- [ ] Update `audio_manifest.py` serialization (both directions)
- [ ] Drop `-select_streams a` from `probe_media`, add subtitle stream parsing
- [ ] Add `choose_subtitle_stream` to `discovery.py`
- [ ] Update existing tests that assert on `SourceMediaProbe` construction

### Phase 2: Extraction and ingest

- [ ] Add `materialize_subtitle` and `build_subtitle_extraction_command` to `materialize.py`
- [ ] Add subtitle extraction to `execute_ingest_plan` (non-fatal on failure)
- [ ] Add `--no-subtitles` flag to CLI
- [ ] Add `_subs/` directory creation and `subtitle_filename` naming
- [ ] Add subtitle verification via `read_subtitle`
- [ ] Add resume logic for subtitle artifacts
- [ ] Test: ingest with embedded EN subs, verify `_subs/S01E001.eng.srt` appears
- [ ] Test: ingest with no embedded subs, verify episode completes with `subtitle=None`

### Phase 3: Service and API

- [ ] Add `subtitle` table to `db.py` schema
- [ ] Add `fetch_subtitle` and `fetch_subtitle_content` queries
- [ ] Extend `load_manifest` (service-side) to extract subtitle rows
- [ ] Add `/series/{id}/episodes/{key}/subtitle` and `/content` endpoints
- [ ] Enrich `/inventory` with `episode_subtitle_count`
- [ ] Add `AnimeAudioSubtitle`, `subtitle()`, `subtitle_content()` to core SDK
- [ ] Add service tests for subtitle indexing and content serving
- [ ] Update docsite (`site/src/content/docs/services/anime-audio.md`)

## File Size Impact

| File | Current | Change | After |
|------|----------|--------|-------|
| `packages/core/.../audio_library.py` | 172 | +20 | ~192 |
| `packages/core/.../audio_manifest.py` | 125 | +15 | ~140 |
| `packages/core/.../anime_audio.py` | 250 | +40 | ~290 |
| `packages/frontend/.../discovery.py` | 202 | +35 | ~237 |
| `packages/frontend/.../materialize.py` | 174 | +50 | ~224 |
| `packages/frontend/.../wizard.py` | 293 | +30 | ~323 ⚠ |
| `envs/services/.../db.py` | 294 | +35 | ~329 ⚠ |
| `envs/services/.../app.py` | 209 | +30 | ~239 |
| `envs/services/.../manifest.py` | 83 | +15 | ~98 |
| `envs/services/.../index.py` | 260 | +15 | ~275 |

Two files approach the 300-line soft limit (`wizard.py`, `db.py`). If either
crosses 300 during implementation, extract the subtitle-specific logic into
a sibling module:

- `wizard.py` → `subtitle_plan.py` (subtitle selection + extraction orchestration)
- `db.py` → `subtitle_db.py` (subtitle table DDL + queries)

This keeps both files well under the 500-line hard limit and preserves the
single-responsibility boundary.
