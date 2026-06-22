# Derived Anime Audio Library

## Status

Phase 1 is implemented as a manual, filesystem-first CLI. Later phases add an
index and automation without invalidating the Phase 1 artifacts or manifests.

## Problem

Anime source files are usually large MKV containers. Extracting one complete
audio stream from an MKV requires reading through the interleaved container,
even when video is not decoded. Running that extraction from a client over NFS
therefore transfers most of the source file.

This is not only a subsync startup problem. The same audio is useful for:

- repeated listening on a phone;
- subtitle timing review;
- ASR and VAD inputs;
- shadowing and sentence-mining tools;
- future clip and transcript generation.

The expensive read should happen once, close to the source storage. The result
should be a durable derived artifact rather than an opaque transcoder cache.

## Goals

- Materialize portable anime audio beside the storage containing the MKVs.
- Organize output so Audiobookshelf can present each anime as a podcast and
  each source episode as an independently tracked podcast episode.
- Keep AniList identity, source provenance, episode mapping, and conversion
  details in a repository-owned manifest.
- Make Phase 1 useful without a database or always-running service.
- Allow Phase 2 to index Phase 1 output without renaming or regenerating it.
- Keep source discovery, metadata, conversion, storage, indexing, and playback
  as explicit boundaries.
- Prefer user confirmation over clever filename inference in Phase 1.
- Make the automated Phase 2 route deterministic: explicit source directory
  plus AniList ID, with no fuzzy title decision hidden inside a service call.

## Non-Goals

- No Jellyfin, Sonarr, or filesystem-event bridge in Phase 1 or Phase 2.
- No direct mutation of source MKVs.
- No requirement that Audiobookshelf become the authoritative media index.
- No public-internet API or multi-tenant path access.
- No arbitrary remote path execution supplied by REST callers.
- No S3 requirement in the first implementation.
- No attempt to solve AniList/TVDB episode-order disagreements automatically.

## Ownership

The durable ownership rule is:

```text
ja-media manifests/index       Audiobookshelf
------------------------       --------------
AniList identity               playback progress
source files                   offline downloads
episode mapping                playlists
derived artifacts              user-facing browsing
conversion provenance          optional metadata edits
```

Audiobookshelf is a projection over generated files. Its database may be
deleted and rebuilt without losing source identity or conversion history.

## Deployment Shape

Audiobookshelf should run in the existing Docker VM, but as a separate Compose
project from the root `ja-media-services` stack.

```text
Docker VM
├── ja-media-services          repository-owned APIs
└── audiobookshelf             third-party playback application

Storage host / NFS server
├── anime source tree          MKVs
└── derived anime audio        generated M4A files and manifests
```

The repository should eventually contain:

```text
deploy/audiobookshelf/
├── compose.yaml
├── .env.example
└── README.md
```

Example deployment contract:

```yaml
name: audiobookshelf

services:
  audiobookshelf:
    image: ghcr.io/advplyr/audiobookshelf:${AUDIOBOOKSHELF_VERSION}
    restart: unless-stopped
    ports:
      - "${AUDIOBOOKSHELF_PORT:-13378}:80"
    volumes:
      - ${AUDIOBOOKSHELF_CONFIG_DIR}:/config
      - ${AUDIOBOOKSHELF_METADATA_DIR}:/metadata
      - ${ANIME_AUDIO_LIBRARY_DIR}:/audio:ro
```

The README should show a host bind mount example such as:

```dotenv
AUDIOBOOKSHELF_VERSION=<pinned-version>
AUDIOBOOKSHELF_PORT=13378
AUDIOBOOKSHELF_CONFIG_DIR=/var/lib/audiobookshelf/config
AUDIOBOOKSHELF_METADATA_DIR=/var/lib/audiobookshelf/metadata
ANIME_AUDIO_LIBRARY_DIR=/mnt/media-derived/anime-audio
```

`/config` and `/metadata` must live on local VM storage. Only `/audio` should
be NFS-backed. Do not put Audiobookshelf's SQLite state on NFS.

After first startup, create one Audiobookshelf **podcast** library rooted at
`/audio`. Do not create an audiobook library: podcast episodes retain
independent progress and new files append without shifting a single
series-global timeline. Phase 1 should print that a library scan is required
after publishing; API-triggered Audiobookshelf scans are explicitly deferred
until there is evidence that manual or scheduled scanning is inadequate.

## Canonical Filesystem Layout

The destination is both a durable artifact store and an Audiobookshelf podcast
library:

```text
<destination-root>/
└── anilist-<id>/
    ├── .ja-media.json
    ├── metadata.json
    ├── cover.jpg
    ├── S01E001.m4a
    ├── S01E002.m4a
    └── S01E003.m4a
```

Rules:

- One AniList media ID maps to one immediate destination directory.
- One audio file maps to one Audiobookshelf podcast episode.
- Episode filenames are normalized and zero-padded.
- No nested `episodes/` directory is required for the Audiobookshelf-facing
  tree.
- Temporary output uses a sibling name such as `.S01E003.m4a.partial` and is
  atomically renamed only after ffprobe verification succeeds.
- `.ja-media.json` is authoritative for ja-media.
- `metadata.json` and embedded audio tags are projections for Audiobookshelf.

AniList commonly gives a sequel season or separately listed cour a distinct
media ID. This layout follows AniList identity rather than trying to construct
a franchise-wide season model in Phase 1.

## Episode Keys

Do not make episode identity integer-only. The durable type is a string:

```text
"1"
"12"
"12.5"
"SP1"
```

Phase 1 may initially accept only positive integer mappings from PTN for
automatic conversion. The manifest schema must still permit future special and
fractional keys without migration.

Normalized filenames for ordinary episodes use:

```text
S01E001.m4a
```

Special keys require an explicit safe filename mapping recorded in the
manifest rather than an improvised parser convention.

## AniList Metadata Input

Use the existing AniList search service:

```text
GET /api/v1/anilist/search
GET /api/v1/anilist/anime/{anilist_id}
```

The implementation should use `HttpAniListSearchClient`, not construct HTTP
paths itself. Service discovery already resolves the active gateway from
`[services].root_url` in the normal ja-media config. Do not add a separate
required hostname to the ingest command.

The configured service was verified on 2026-06-21 through:

```sh
curl \
  "http://magi06-ja-media-toolkit/api/v1/anilist/anime/154587?fields=title_romaji,title_english,title_native,title_userPreferred,description,format,status,season,seasonInt,seasonYear,episodes,duration,startDate_year,startDate_month,startDate_day,endDate_year,endDate_month,endDate_day,genres,source,countryOfOrigin,coverImage_color,coverImage_extraLarge,coverImage_large,coverImage_medium,bannerImage,idMal,siteUrl,updatedAt"
```

Do not hard-code that hostname in application code; it is shown only as the
actual verification command. `HttpAniListSearchClient` resolved it from the
active config.

The verified response was:

```json
{
  "title_romaji": "Sousou no Frieren",
  "title_english": "Frieren: Beyond Journey’s End",
  "title_native": "葬送のフリーレン",
  "title_userPreferred": "Sousou no Frieren",
  "description": "The adventure is over ...<br><br>\n(Source: Crunchyroll)",
  "format": "TV",
  "status": "FINISHED",
  "season": "FALL",
  "seasonInt": 234.0,
  "seasonYear": 2023.0,
  "episodes": 28.0,
  "duration": 24.0,
  "startDate_year": 2023,
  "startDate_month": 9.0,
  "startDate_day": 29.0,
  "endDate_year": 2024.0,
  "endDate_month": 3.0,
  "endDate_day": 22.0,
  "genres": "[\"Adventure\", \"Drama\", \"Fantasy\"]",
  "source": "MANGA",
  "countryOfOrigin": "JP",
  "coverImage_color": "#bbf1a1",
  "coverImage_extraLarge": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx154587-qQTzQnEJJ3oB.jpg",
  "coverImage_large": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/bx154587-qQTzQnEJJ3oB.jpg",
  "coverImage_medium": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/small/bx154587-qQTzQnEJJ3oB.jpg",
  "bannerImage": "https://s4.anilist.co/file/anilistcdn/media/anime/banner/154587-ivXNJ23SM1xB.jpg",
  "idMal": 52991.0,
  "siteUrl": "https://anilist.co/anime/154587",
  "updatedAt": 1781420427,
  "anilist_id": 154587
}
```

The extra-large cover URL was also downloaded successfully as a 460×649 JPEG.
Cover selection should therefore prefer:

1. `coverImage_extraLarge`;
2. `coverImage_large`;
3. `coverImage_medium`;
4. no cover.

### What Is in the Cached AniList Row

A full-row curl against AniList `154587` returned these fields:

```text
airingSchedule
anilist_id
averageScore
bannerImage
chapters
characters
column00
countryOfOrigin
coverImage_color
coverImage_extraLarge
coverImage_large
coverImage_medium
description
duration
endDate_day
endDate_month
endDate_year
episodes
externalLinks
favourites
format
genres
hashtag
idMal
isAdult
isFavourite
isLicensed
isLocked
meanScore
nextAiringEpisode
popularity
rankings
recommendations
relations
reviews
season
seasonInt
seasonYear
siteUrl
source
staff
startDate_day
startDate_month
startDate_year
stats_scoreDistribution
stats_statusDistribution
status
streamingEpisodes
studios
synonyms
tags
title_english
title_native
title_romaji
title_userPreferred
trailer_id
trailer_site
trailer_thumbnail
trending
type
updatedAt
volumes
```

Phase 1 only needs the title, description, format/status, date, episode-count,
genre, source, country, cover/banner, MAL, site URL, and update fields listed
below. Keep that selected raw payload in the manifest. The remaining field
inventory documents what later phases can fetch by the already-confirmed
AniList ID; Phase 1 should not bloat every manifest with large character,
staff, review, and recommendation blobs. `column00` is an upstream dataset
artifact and must not become a domain field.

The cached Kaggle schema may evolve, so the adapter owns source-field aliases
and normalization. The normalized series contract uses:

| Manifest value | Candidate AniList field |
| --- | --- |
| AniList ID | `anilist_id` |
| English title | `title_english` |
| Japanese/native title | `title_native` |
| Romaji title | `title_romaji` |
| Description | `description` |
| Format | `format` |
| Season | `season` |
| Season year | `seasonYear` |
| Expected episode count | `episodes` |
| Typical duration | `duration` |
| Start/end dates | `startDate_*`, `endDate_*` |
| Status | `status` |
| Genres | `genres` |
| Source medium | `source` |
| Country | `countryOfOrigin` |
| Cover URL | `coverImage_extraLarge`, then `coverImage_large`, then `coverImage_medium` |
| Banner URL | `bannerImage` |
| MAL ID | `idMal` |
| AniList page | `siteUrl` |
| Source snapshot version | `updatedAt` |

### AniList Normalization Contract

The service exposes a CSV-derived record, not a perfectly normalized domain
object. The Phase 1 metadata adapter must:

- convert integer-valued floats such as `28.0`, `2023.0`, and `52991.0` to
  integers;
- preserve `null` rather than inventing zeroes;
- decode `genres` when it arrives as a JSON-encoded string;
- accept an already-decoded list if the service later normalizes `genres`;
- keep the raw selected field payload in `metadata_snapshot`;
- preserve the original HTML-bearing description in the snapshot;
- derive a plain-text description for audio tags and Audiobookshelf metadata;
- reject a detail response whose returned `anilist_id` differs from the
  requested ID;
- tolerate newly added fields and ignore them outside the raw snapshot.

Suggested core contract:

```python
@dataclass(frozen=True)
class AnimeAudioSeriesMetadata:
    """Normalized AniList metadata used by audio-library manifests."""

    anilist_id: int
    title_english: str | None
    title_native: str | None
    title_romaji: str | None
    title_preferred: str
    description_html: str | None
    description_text: str | None
    format: str | None
    status: str | None
    season: str | None
    season_year: int | None
    episode_count: int | None
    typical_duration_minutes: int | None
    start_date: date | None
    end_date: date | None
    genres: tuple[str, ...]
    source: str | None
    country_of_origin: str | None
    cover_url: str | None
    banner_url: str | None
    mal_id: int | None
    site_url: str | None
    upstream_updated_at: int | None
    raw_snapshot: Mapping[str, object]
```

Preferred display title order:

1. `title_english`;
2. `title_romaji`;
3. `title_native`;
4. `title_userPreferred`;
5. `AniList <id>`.

The native title is always retained separately even when it is not selected as
the display title.

If no cover URL is available, Phase 1 finishes without `cover.jpg`; cover
absence is not an ingest failure. Cover download must validate an image
response before atomic publication rather than trusting the URL extension.
Use response size limits plus ffprobe to verify that the temporary file has one
decodable video/image stream and to record width and height. This avoids adding
an image library solely for cover validation.

### Search Confirmation Contract

The live fuzzy search was also verified. Searching for `Sousou no Frieren`
ranked two related chibi ONA entries above AniList `154587`, the intended TV
series. Therefore:

- fuzzy search is candidate generation only;
- the wizard must never auto-select the first result;
- every fuzzy result requires explicit user confirmation;
- show ID, all three titles, format, season, year, and score;
- fetching and confirming the full detail row is a separate step;
- explicit `--anilist` skips fuzzy search but still shows the detail summary
  before conversion in Phase 1.

## Phase 1: Manual Ingest

### Deliverables

```text
deploy/audiobookshelf/
packages/core/src/ja_media_core/audio_library.py
packages/frontend/src/ja_media_frontend/audio_library/
packages/frontend/src/ja_media_frontend/audio_library/cli.py
packages/frontend/src/ja_media_frontend/audio_library/wizard.py
packages/frontend/src/ja_media_frontend/audio_library/discovery.py
packages/frontend/src/ja_media_frontend/audio_library/materialize.py
packages/frontend/tests/test_audio_library_*.py
```

Keep each module focused and below the repository line limits. Do not add the
workflow to the already oversized subsync TUI module.

### Phase 1 Module Contracts

`packages/core/src/ja_media_core/audio_library.py` owns serializable domain
contracts only:

```python
@dataclass(frozen=True)
class AudioStreamProbe:
    global_index: int
    audio_ordinal: int
    codec: str
    language: str | None
    title: str | None
    channels: int | None
    sample_rate_hz: int | None
    default: bool


@dataclass(frozen=True)
class SourceMediaProbe:
    path: Path
    duration_ms: int
    size_bytes: int
    mtime_ns: int
    audio_streams: tuple[AudioStreamProbe, ...]


@dataclass(frozen=True)
class EpisodeMapping:
    episode_key: str
    source_path: Path
    stream: AudioStreamProbe


@dataclass(frozen=True)
class AudioProfile:
    name: str
    container: str
    codec: str
    bitrate_bps: int
    max_channels: int
    sample_rate_hz: int


@dataclass(frozen=True)
class MaterializationPlan:
    source_root: Path
    destination_root: Path
    series: AnimeAudioSeriesMetadata
    mappings: tuple[EpisodeMapping, ...]
    profile: AudioProfile
```

Pydantic models may replace dataclasses when JSON schema emission is useful,
but filesystem and subprocess behavior must remain outside core.

`discovery.py` owns deterministic discovery and probing:

```python
def discover_media(source_dir: Path) -> tuple[Path, ...]: ...
def suggest_episode_key(path: Path) -> str | None: ...
def probe_media(path: Path) -> SourceMediaProbe: ...
def choose_unambiguous_audio_stream(
    probe: SourceMediaProbe,
    *,
    preferred_languages: tuple[str, ...] = ("jpn", "ja"),
) -> AudioStreamProbe | None: ...
```

`metadata.py` owns AniList adaptation and cover retrieval:

```python
def normalize_anilist_metadata(metadata: AnimeMetadata) -> AnimeAudioSeriesMetadata: ...
def description_to_plain_text(description_html: str | None) -> str | None: ...
def choose_cover_url(metadata: AnimeMetadata) -> str | None: ...
def download_cover(url: str, destination: Path) -> CoverArtifact: ...
```

`materialize.py` owns conversion:

```python
def build_ffmpeg_command(
    mapping: EpisodeMapping,
    destination: Path,
    series: AnimeAudioSeriesMetadata,
    profile: AudioProfile,
) -> list[str]: ...

def materialize_episode(
    mapping: EpisodeMapping,
    destination: Path,
    series: AnimeAudioSeriesMetadata,
    profile: AudioProfile,
) -> ArtifactRecord: ...

def verify_audio_artifact(path: Path, profile: AudioProfile) -> ArtifactRecord: ...
```

`manifest.py` owns atomic persistence:

```python
def load_manifest(path: Path) -> AnimeAudioManifest: ...
def write_manifest_atomic(path: Path, manifest: AnimeAudioManifest) -> None: ...
def project_audiobookshelf_metadata(
    manifest: AnimeAudioManifest,
) -> dict[str, object]: ...
```

`wizard.py` owns interaction and returns a complete plan before conversion:

```python
def build_ingest_plan(request: IngestWizardRequest) -> MaterializationPlan | None: ...
def execute_ingest_plan(plan: MaterializationPlan) -> IngestSummary: ...
```

The wizard should depend on a small prompt protocol so decision logic can be
tested without driving a terminal:

```python
class WizardPrompts(Protocol):
    def choose_anime(self, candidates: Sequence[SearchResult]) -> int | None: ...
    def confirm_series(self, metadata: AnimeAudioSeriesMetadata) -> bool: ...
    def edit_episode_mappings(
        self, suggestions: Sequence[EpisodeMappingSuggestion]
    ) -> Sequence[EpisodeMappingDecision]: ...
    def choose_audio_stream(
        self, source: SourceMediaProbe
    ) -> AudioStreamProbe | None: ...
    def confirm_plan(self, plan: MaterializationPlan) -> bool: ...
```

### CLI

Proposed entrypoint:

```sh
ja-media audio-library ingest \
  --source "/srv/anime/Series Folder" \
  --destination "/srv/derived/anime-audio"
```

Optional explicit identity:

```sh
ja-media audio-library ingest \
  --source "/srv/anime/Series Folder" \
  --destination "/srv/derived/anime-audio" \
  --anilist 171018
```

Useful controls:

```text
--profile portable-aac-v1
--audio-stream 0
--language jpn
--dry-run
--resume
--replace
```

Phase 1 is an interactive wizard by default. Noninteractive behavior belongs
to Phase 2 rather than a growing collection of unsafe Phase 1 flags.

### Wizard Steps

1. Validate that source and destination directories exist and that `ffmpeg`
   and `ffprobe` are available.
2. Resolve AniList identity:
   - use `--anilist` when supplied;
   - otherwise search using only the immediate source directory name;
   - request all AniList formats so movies, OVAs, specials, and ONAs are not
     silently excluded;
   - show ranked matches with ID, English/native/romaji titles, year, season,
     and format;
   - allow selection, a manually entered AniList ID, a new search string, or
     cancellation.
3. Fetch the selected full metadata row and show a confirmation summary.
4. Discover immediate-directory media files. Phase 1 should not recurse unless
   a later explicit option adds that behavior.
5. Use PTN on each filename stem to suggest episode mappings.
6. Present a mapping table containing source filename, parsed anime title,
   parsed episode value, duration, and detected audio streams.
7. Require the user to approve, edit, exclude, or abort mappings.
8. Reject duplicate episode keys until the user resolves them.
9. Probe each approved source with ffprobe and choose an audio stream:
   - honor explicit `--audio-stream`;
   - otherwise prefer a stream tagged `jpn`/`ja`;
   - when selection remains ambiguous, ask once if the same stream layout is
     shared, or ask per file.
10. Show the complete execution plan and estimated output paths.
11. Create the destination series directory.
12. Download the cover, if available, to a temporary file and atomically
    publish `cover.jpg`.
13. Transcode each approved source to a temporary artifact.
14. Verify codec, duration, channel count, sample rate, and nonzero size with
    ffprobe.
15. Atomically publish each artifact.
16. Write `metadata.json` and `.ja-media.json` atomically.
17. Print a summary of created, skipped, failed, and resumable artifacts.

The wizard state transitions are:

```text
validate
  → resolve_identity
  → confirm_metadata
  → discover_sources
  → confirm_episode_mapping
  → resolve_audio_streams
  → confirm_plan
  → materialize
  → publish_metadata
  → summarize
```

Cancellation before `materialize` writes nothing. Cancellation or failure
during `materialize` preserves already published artifacts and the latest
atomic manifest checkpoint.

### Discovery Rules

- Supported source extensions initially: `.mkv`, `.mp4`, `.m4v`, `.webm`.
- PTN supplies suggestions; it does not silently establish truth.
- Files with no parsed episode remain unmapped and require user action.
- Multi-episode parser output must be rejected in Phase 1 unless the user maps
  it to a single episode explicitly.
- Fractional and special episodes require explicit keys.
- Input order is irrelevant after mappings are confirmed.

The existing PTN normalization in `ja_media_core.subsync` should be extracted
into a media-filename primitive rather than copied into a third implementation.

### Default Audio Profile

Start with one conservative portable profile:

```text
name: portable-aac-v1
container: M4A
codec: AAC-LC
bitrate: 128 kbps
maximum channels: 2
sample rate: 48 kHz
language: jpn
```

This is a listening artifact, not an ASR intermediate. Preserve a conventional
stereo presentation and enough bitrate for openings, endings, music, and sound
design. A later `review-opus-v1` or `asr-pcm-v1` profile may downmix to mono.
For mono sources, preserve mono rather than upmixing. The command below is the
stereo-source shape; command construction sets `-ac 1` for a mono source. The
verified output channel count is recorded per artifact.

Representative ffmpeg shape:

```sh
ffmpeg -hide_banner -nostdin -i SOURCE \
  -map 0:GLOBAL_STREAM_INDEX -vn \
  -ac 2 -ar 48000 \
  -c:a aac -b:a 128k \
  -movflags +faststart \
  -metadata language=jpn \
  -metadata album="SERIES_TITLE" \
  -metadata title="Episode EPISODE_KEY" \
  -metadata track="EPISODE_NUMBER" \
  OUTPUT.partial.m4a
```

The exact argument construction must be tested as a value list, not assembled
as a shell string. A later profile can add Opus or codec-copy output.

### Audiobookshelf Metadata

Embed at least:

```text
album        = preferred series title
album_artist = Japanese Animation
title        = Episode <key> [— episode title when known]
track        = ordinary numeric episode number
disc         = 1
date         = air date when known
language     = jpn
genre        = Anime
```

Write Audiobookshelf's supported `metadata.json` with fields such as:

```json
{
  "title": "Frieren: Beyond Journey’s End",
  "author": "Japanese Animation",
  "description": "The adventure is over ...",
  "releaseDate": "2023-09-29",
  "genres": ["Anime", "Adventure", "Drama", "Fantasy"],
  "tags": ["ja-media", "anime", "anilist:154587", "mal:52991"],
  "language": "ja",
  "explicit": false,
  "podcastType": "episodic"
}
```

Audiobookshelf has a fixed metadata schema, not arbitrary custom fields.
AniList IDs in tags are a useful projection but not authoritative identity.

### Canonical Manifest

`.ja-media.json` uses a versioned schema:

```json
{
  "schema_version": 1,
  "kind": "anime-audio-series",
  "series": {
    "anilist_id": 154587,
    "title_english": "Frieren: Beyond Journey’s End",
    "title_native": "葬送のフリーレン",
    "title_romaji": "Sousou no Frieren",
    "title_preferred": "Frieren: Beyond Journey’s End",
    "description_html": "The adventure is over ...<br><br>\n(Source: Crunchyroll)",
    "description_text": "The adventure is over ...\n\n(Source: Crunchyroll)",
    "format": "TV",
    "status": "FINISHED",
    "season": "FALL",
    "season_year": 2023,
    "episode_count": 28,
    "typical_duration_minutes": 24,
    "start_date": "2023-09-29",
    "end_date": "2024-03-22",
    "genres": ["Adventure", "Drama", "Fantasy"],
    "source": "MANGA",
    "country_of_origin": "JP",
    "mal_id": 52991,
    "site_url": "https://anilist.co/anime/154587",
    "upstream_updated_at": 1781420427,
    "cover": {
      "source_url": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx154587-qQTzQnEJJ3oB.jpg",
      "path": "cover.jpg",
      "media_type": "image/jpeg",
      "width": 460,
      "height": 649,
      "size_bytes": 136267
    },
    "banner_url": "https://s4.anilist.co/file/anilistcdn/media/anime/banner/154587-ivXNJ23SM1xB.jpg",
    "metadata_snapshot": {
      "seasonYear": 2023.0,
      "episodes": 28.0,
      "genres": "[\"Adventure\", \"Drama\", \"Fantasy\"]"
    }
  },
  "profile": {
    "name": "portable-aac-v1",
    "container": "m4a",
    "codec": "aac",
    "bitrate_bps": 128000,
    "max_channels": 2,
    "sample_rate_hz": 48000
  },
  "episodes": [
    {
      "episode_key": "1",
      "source": {
        "relative_path": "Series - 01.mkv",
        "size_bytes": 1234567890,
        "mtime_ns": 1710000000000000000,
        "global_stream_index": 2,
        "audio_stream_ordinal": 1,
        "audio_codec": "flac",
        "audio_language": "jpn"
      },
      "artifact": {
        "relative_path": "S01E001.m4a",
        "size_bytes": 12345678,
        "duration_ms": 1440000,
        "codec": "aac",
        "bitrate_bps": 128000,
        "channels": 2,
        "sample_rate_hz": 48000,
        "sha256": "optional-in-phase-1"
      },
      "created_at": "2026-06-21T18:00:00Z"
    }
  ]
}
```

`source.relative_path` is relative to the user-supplied source root. Avoid
making a host-specific absolute path the only provenance.

`global_stream_index` is ffprobe's stream `index` and is used with
`ffmpeg -map 0:<index>`. `audio_stream_ordinal` is the zero-based position among
audio streams and is retained for diagnostics and user-facing selection. Do
not pass an audio ordinal to the global-index form of `-map`.

The source fingerprint is initially `(relative path, size, mtime_ns)`. SHA-256
of multi-gigabyte MKVs is optional because it adds another complete source
read. Artifact hashing is cheap enough to support but may also be deferred.

### Resume and Replacement

- Existing verified artifact plus matching source fingerprint and profile:
  skip.
- Existing artifact absent from the manifest: stop and ask; do not adopt it
  silently.
- Manifest entry with missing artifact: regenerate.
- Source fingerprint changed: mark stale and require confirmation.
- Profile changed: write a distinct artifact or require `--replace`; never
  pretend the old artifact satisfies the new profile.
- Failure on one episode must not remove previously completed episodes.
- Rewrite the manifest after each successfully published episode so an
  interrupted batch is resumable.

## Phase 2: Indexed Service

### Purpose

Phase 2 makes the filesystem artifacts addressable by stable identity and
supports deterministic automated ingestion. It does not make Audiobookshelf
authoritative.

### Startup Contract

The service receives explicit roots:

```text
SOURCE_ROOT=/srv/anime
DESTINATION_ROOT=/srv/derived/anime-audio
INDEX_DB_PATH=/var/lib/anime-audio/index.sqlite
```

Equivalent command:

```sh
anime-audio-service \
  --source-root /srv/anime \
  --destination-root /srv/derived/anime-audio \
  --index-db /var/lib/anime-audio/index.sqlite
```

Source paths submitted through the API must be relative to `SOURCE_ROOT`.
Resolve and verify paths before use; reject traversal and symlink escape.

### Indexing

At startup and on demand:

1. Scan immediate children of `DESTINATION_ROOT`.
2. Read `.ja-media.json`.
3. Validate schema version and artifact existence.
4. Upsert series, episode, source, and artifact rows.
5. Record malformed manifests as reconciliation errors rather than dropping
   previously indexed rows silently.

Suggested SQLite tables:

```text
series
  anilist_id PK
  title_english
  title_native
  title_romaji
  manifest_path UNIQUE
  manifest_mtime_ns

episodes
  anilist_id FK
  episode_key
  source_relative_path
  source_size_bytes
  source_mtime_ns
  global_stream_index
  audio_stream_ordinal
  PRIMARY KEY (anilist_id, episode_key)

artifacts
  anilist_id FK
  episode_key FK
  profile
  relative_path
  size_bytes
  duration_ms
  status
  UNIQUE (anilist_id, episode_key, profile)

jobs
  id PK
  operation
  status
  request_json
  result_json
  error
  created_at
  updated_at
```

SQLite is service-local state and must not live on NFS. Manifests remain the
rebuildable source of truth.

### Read API

```text
GET /api/v1/audio/series/{anilist_id}
GET /api/v1/audio/series/{anilist_id}/episodes
GET /api/v1/audio/series/{anilist_id}/episodes/{episode_key}
GET /api/v1/audio/series/{anilist_id}/episodes/{episode_key}/artifacts/{profile}
GET /api/v1/audio/series/{anilist_id}/episodes/{episode_key}/content?profile=portable-aac-v1
GET /api/v1/audio/jobs/{job_id}
POST /api/v1/audio/reconcile
GET /healthz
GET /stats
```

The artifact metadata endpoint returns a typed record. The content endpoint
streams or delegates file serving; clients must not infer filesystem paths.

Example artifact response:

```json
{
  "anilist_id": 171018,
  "episode_key": "4",
  "profile": "portable-aac-v1",
  "media_type": "audio/mp4",
  "size_bytes": 12001234,
  "duration_ms": 1439123,
  "etag": "\"artifact-fingerprint\"",
  "content_url": "/api/v1/audio/series/171018/episodes/4/content?profile=portable-aac-v1"
}
```

### Automated Ingest API

Optimistic route:

```text
POST /api/v1/audio/ingests
```

```json
{
  "source_directory": "Currently Airing/Series Folder",
  "anilist_id": 171018,
  "profile": "portable-aac-v1"
}
```

Semantics:

- `source_directory` is relative to configured `SOURCE_ROOT`.
- `anilist_id` is mandatory.
- No fuzzy title search occurs.
- Discover immediate media files.
- Parse episode suggestions with PTN.
- Reject the whole request on missing episode values, duplicate mappings,
  unsupported multi-episode files, or ambiguous audio-stream selection.
- Do not guess through ambiguity merely because this endpoint is automated.
- Return `202 Accepted` with a job ID after validation.
- Materialize idempotently using the same Phase 1 implementation functions.

This route tests whether real-world naming is safe enough for later event-driven
automation. Its rejection details are part of the product: they show which
heuristics need improvement without corrupting the library.

### Core SDK

Add dependency-light contracts and a synchronous HTTP client under
`packages/core`, following the existing crosswalk and AniList clients:

```python
class AnimeAudioClient(Protocol):
    def series(self, anilist_id: int) -> AnimeAudioSeries: ...
    def episodes(self, anilist_id: int) -> tuple[AnimeAudioEpisode, ...]: ...
    def artifact(
        self,
        anilist_id: int,
        episode_key: str,
        *,
        profile: str = "portable-aac-v1",
    ) -> AnimeAudioArtifact: ...
    def ingest(self, request: AnimeAudioIngestRequest) -> AnimeAudioJob: ...
```

`subsync` can then accept:

```sh
ja-media subsync tui --anilist 171018 --episode 4
```

It resolves an artifact through `AnimeAudioClient`, downloads/caches it
locally, decodes it for playback, and never reads the original MKV over NFS.

### Service Package Boundary

```text
packages/core/src/ja_media_core/anime_audio.py
envs/services/src/ja_media_services/anime_audio/
├── app.py
├── settings.py
├── db.py
├── index.py
├── ingest.py
├── materialize.py
├── models.py
└── smoke.py
```

The Phase 1 frontend wizard and Phase 2 service must share contracts and
focused implementation helpers. Do not have the service invoke the interactive
CLI as a subprocess.

## Phase 3: Storage and Worker Separation

- Introduce `ArtifactStore` with filesystem and S3-compatible implementations.
- Keep manifests addressable even when artifact bytes move to object storage.
- Separate scheduler/API from the ffmpeg worker.
- Add a polling worker or constrained job lease protocol so conversion can run
  physically beside source storage.
- Add multiple named profiles, including compact Opus, source-codec copy, and
  ASR/review representations.
- Add retention and garbage-collection reports, but require explicit deletion.

## Phase 4: External Inventory Bridges

- Add Jellyfin as an optional source-discovery adapter, not an identity owner.
- Add Sonarr/TVDB inputs through the anime crosswalk service.
- Store external item IDs as aliases on source records.
- Accept webhooks only as acceleration signals; retain periodic reconciliation.
- Require kind-aware TVDB/TMDB resolution and explicit handling of ambiguous
  crosswalk matches.

## Phase 5: Rich Episode Identity

- Model specials, fractional episodes, absolute numbering, and multi-episode
  source files.
- Use AniList relations to navigate sequels, split cours, OVAs, and specials
  without collapsing them into one false season model.
- Add explicit user-maintained corrections that survive metadata refreshes.
- Attach episode titles and air dates from a suitable episode-level source;
  AniList's series record alone is not sufficient.

## Phase 6: Additional Consumers

- Generate RSS directly when a client should not depend on Audiobookshelf.
- Feed durable audio artifacts into ASR, VAD, shadowing, and clip-generation
  workflows.
- Add transcript and subtitle artifacts to the same provenance model.
- Publish static catalog/search surfaces.
- Optionally synchronize publication state with Audiobookshelf while keeping
  its playback progress external.

## Security and Operational Rules

- Treat all REST path values as identifiers or paths relative to configured
  roots.
- Never expose arbitrary host paths in public responses.
- Never interpolate paths into shell commands.
- Run ffmpeg with argument arrays and no shell.
- Cap concurrent transcodes.
- Use temporary files and atomic rename.
- Keep service databases and Audiobookshelf state off NFS.
- Log source-relative paths, job IDs, episode keys, profile names, durations,
  and failures; do not log secrets.
- Make health distinguish API liveness, index readiness, source-root
  availability, destination writability, and worker availability.

## Test Strategy

### Phase 1

- PTN suggestions for common anime filenames.
- Missing, duplicate, fractional, and multi-episode mappings.
- Japanese audio preference and ambiguous stream selection.
- Exact ffmpeg argument construction.
- Partial-file cleanup and atomic publication.
- Resume after interruption.
- Source fingerprint changes.
- Manifest schema round trips.
- Audiobookshelf metadata projection.
- Cover download absence and failure.
- AniList integer-valued float normalization.
- AniList `genres` as both a JSON string and an already-decoded list.
- Fuzzy-search results never auto-select the highest score.

Use generated tiny fixtures rather than committing large media. Tests that
need ffmpeg should be marked and skipped when ffmpeg is unavailable.

### Phase 2

- Rebuild SQLite entirely from manifests.
- Idempotent reconciliation.
- Malformed manifest reporting.
- Root traversal and symlink escape rejection.
- Automated ingest rejection on ambiguity.
- Stable `(AniList ID, episode key, profile)` lookup.
- HTTP range/content behavior where supported.
- SDK URL construction and error mapping without a live service.

## Implementation Order

1. Define versioned manifest, profile, source, mapping, artifact, and job
   contracts in `packages/core`.
2. Extract reusable PTN episode-key parsing from subsync-specific code.
3. Implement discovery and ffprobe models.
4. Implement the Phase 1 wizard as a pure decision flow over injectable I/O.
5. Implement atomic ffmpeg materialization and verification.
6. Implement AniList metadata projection, cover download, and manifest writes.
7. Add the Audiobookshelf Compose deployment and operator documentation.
8. Validate the generated directory with a real Audiobookshelf podcast library.
9. Implement Phase 2 manifest indexing and read API.
10. Add the HTTP SDK and switch subsync's remote identity path to it.
11. Add automated ingest only after manual runs provide representative filename
    and stream-selection failures.

## Acceptance Criteria

Phase 1 is complete when a user can SSH to the storage host, run one wizard
against a series directory, confirm mappings, and obtain an Audiobookshelf
podcast directory with resumable, verified audio artifacts and a complete
`.ja-media.json`.

Phase 2 is complete when the service can rebuild its entire index from those
manifests, resolve an audio artifact by AniList ID and episode key, and perform
a deterministic ingest from an explicit source-relative directory without
interactive or fuzzy decisions.
