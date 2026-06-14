# LAN Kitsunekko Subtitle Service

Build a small LAN-only subtitle inventory and retrieval service over the local
Kitsunekko GitHub mirror. The service should follow the anime crosswalk
service's operating model: clone or update an upstream repository inside the
container, build a generated SQLite database, smoke-test it, atomically promote
it, and expose a boring FastAPI API for local tools and agents.

The immediate goal is to stop manually traversing the Kitsunekko mirror. A user
or tool should be able to ask whether subtitles exist for a known anime ID,
inspect available files, and fetch subtitle content without caring about the
mirror's directory layout.

## Goals

- Mirror or update the Kitsunekko GitHub repository inside the service
  container.
- Rebuild the generated subtitle index on a short timer. The mirror appears to
  update roughly every 3-6 hours, so start with a 1 hour poll interval.
- Index subtitle files by AniList ID from the existing filename inventory /
  crosswalk-derived mapping.
- Allow lookup by AniList ID and TVDB ID in v0.
- Return available subtitle files for a series, including at least:
  - filename;
  - repository path;
  - extension;
  - parsed episode candidates;
  - leading group / release tag hints;
  - language hints;
  - last modified metadata if available.
- Fetch actual subtitle file content by stable file identifier or path.
- Support gzip transfer for subtitle responses and larger list/export
  responses.
- Provide `/llms.txt` so local agents can discover the service contract.
- Fit into the root Docker Compose stack alongside `anime-crosswalk`.
- Keep the runtime read-heavy and generated-data-oriented. Prefer SQLite with
  atomic replacement over a shared mutable application database.

## Non-Goals

- No public internet exposure.
- No user authentication in v0.
- No writable corrections in v0.
- No Postgres in v0.
- No full-text subtitle corpus search in v0.
- No quality scoring or forced-alignment ranking in v0.
- No object-storage backend in v0.
- No attempt to perfectly normalize all release titles in v0.

## Architecture

Use two generated SQLite databases:

```text
anime-crosswalk
  /var/lib/anime-crosswalk/anime_lists.sqlite

kitsunekko-subtitles
  /var/lib/kitsunekko-subtitles/kitsunekko_subtitles.sqlite
```

At Kitsunekko rebuild time, the ingestor should read the crosswalk database
read-only and materialize lookup rows into its own database. In Docker Compose,
mount the crosswalk data volume read-only into the Kitsunekko container, then
use SQLite `ATTACH` during ingestion:

```sql
ATTACH DATABASE '/var/lib/anime-crosswalk-ro/anime_lists.sqlite' AS crosswalk;
```

The runtime API should query only the Kitsunekko database. This gives callers
the practical effect of joining subtitle rows against TVDB/AniList IDs without
requiring a shared Postgres server or service-to-service calls on every request.

Store source metadata in the Kitsunekko database:

- Kitsunekko mirror commit.
- Crosswalk source commit.
- build timestamp.
- schema version.
- subtitle row count.
- lookup row count.

The important invariant is that every runtime response can say which source
versions it came from.

## Proposed Schema

Start with a deliberately plain schema:

```sql
CREATE TABLE subtitle_file (
  subtitle_id INTEGER PRIMARY KEY,
  anilist_id INTEGER NOT NULL,
  repo_path TEXT NOT NULL UNIQUE,
  filename TEXT NOT NULL,
  extension TEXT NOT NULL,
  episode_local INTEGER,
  episode_absolute INTEGER,
  episode_raw TEXT,
  episode_confidence TEXT NOT NULL,
  group_hint TEXT,
  language_hint TEXT,
  release_tags_json TEXT NOT NULL,
  last_modified TEXT
);

CREATE INDEX subtitle_file_anilist_idx
ON subtitle_file(anilist_id);

CREATE INDEX subtitle_file_episode_idx
ON subtitle_file(anilist_id, episode_local);

CREATE TABLE subtitle_lookup (
  source TEXT NOT NULL,
  external_id TEXT NOT NULL,
  media_kind TEXT,
  anilist_id INTEGER NOT NULL,
  subtitle_id INTEGER NOT NULL REFERENCES subtitle_file(subtitle_id),
  PRIMARY KEY (source, external_id, media_kind, subtitle_id)
);

CREATE INDEX subtitle_lookup_source_id_idx
ON subtitle_lookup(source, external_id);

CREATE INDEX subtitle_lookup_source_id_kind_idx
ON subtitle_lookup(source, external_id, media_kind);

CREATE TABLE metadata (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
```

Episode parsing should preserve ambiguity. Some filenames contain both
season-local and absolute episode numbers, for example Attack on Titan style
names like `S04E28 (87)`. The index should not collapse these too early. Keep
both a local and absolute candidate when available, plus the raw token and a
confidence label.

## API Sketch

Use paths that are easy to curl and easy for agents to infer:

```text
GET /healthz
GET /stats
GET /metrics
GET /llms.txt

GET /series/anilist/{anilist_id}
GET /series/tvdb/{tvdb_id}
GET /series/tvdb/{media_kind}/{tvdb_id}

GET /series/anilist/{anilist_id}/files
GET /series/tvdb/{tvdb_id}/files
GET /series/tvdb/{media_kind}/{tvdb_id}/files

GET /files/{subtitle_id}
GET /files/{subtitle_id}/content
```

`/series/...` should summarize whether the series exists and what coverage is
available. `/files` should return the concrete subtitle rows. `/content` should
return the subtitle bytes with a useful filename and gzip transfer when the
client accepts it.

Potential later additions:

```text
GET /bulk/anilist/{anilist_id}.tar.gz
GET /bulk/tvdb/{tvdb_id}.tar.gz
GET /export/subtitle-index.jsonl.gz
```

Do not add these until the single-file and file-list contracts feel stable.

## Docker Compose

Add a second service next to `anime-crosswalk`, probably using the same
`envs/services` image at first:

```yaml
kitsunekko-subtitles:
  build:
    context: .
    dockerfile: envs/services/Dockerfile
  image: ja-media/kitsunekko-subtitles:local
  restart: unless-stopped
  ports:
    - "58835:8000"
  environment:
    KITSUNEKKO_SUBTITLES_HOST: "0.0.0.0"
    KITSUNEKKO_SUBTITLES_PORT: "8000"
    KITSUNEKKO_SUBTITLES_DATA_DIR: "/var/lib/kitsunekko-subtitles"
    KITSUNEKKO_SUBTITLES_DB_PATH: "/var/lib/kitsunekko-subtitles/kitsunekko_subtitles.sqlite"
    KITSUNEKKO_SUBTITLES_UPDATE_INTERVAL_SECONDS: "3600"
    ANIME_CROSSWALK_DB_PATH: "/var/lib/anime-crosswalk-ro/anime_lists.sqlite"
  volumes:
    - kitsunekko-subtitles-data:/var/lib/kitsunekko-subtitles
    - anime-crosswalk-data:/var/lib/anime-crosswalk-ro:ro
  depends_on:
    anime-crosswalk:
      condition: service_healthy
```

The ingestor should tolerate crosswalk being unavailable at first boot only if
there is already a usable Kitsunekko DB. Otherwise, fail loudly so Compose can
restart it after crosswalk finishes.

## Update Flow

The entrypoint should mirror `anime-crosswalk`:

1. Ensure the data directory exists.
2. Clone the Kitsunekko mirror if missing.
3. Fetch/reset the mirror to the configured branch.
4. Compare the current mirror commit with the last indexed commit.
5. If unchanged and a DB exists, keep serving.
6. If changed, build `kitsunekko_subtitles.sqlite.next`.
7. During build, read the crosswalk DB and materialize TVDB/AniList lookup rows.
8. Smoke-test the next DB.
9. Move it over the live DB atomically.
10. Restart or reopen the API process.

The timer should default to every 3600 seconds.

## Bulk Subtitle Access

Question: should the service add an S3/Garage backend because subtitle files are
useful in bulk for corpus mining, ASR benchmarks, and diachronic language
analysis?

Recommendation: **YAGNI for v0 and Phase II. Add bulk REST export first.**

The working estimate is around 3.5 GB uncompressed. That is not tiny, but it is
small enough for LAN-local gzip/tar export and direct filesystem reads from the
mirror volume. Adding S3/Garage now would introduce credentials, object naming,
sync semantics, partial failure handling, cache invalidation, and another
storage contract before the service has proven its API shape.

Use this progression:

### Phase I

- Single-file content fetch.
- Series file listing.
- Gzip transfer support.

### Phase II

- Bulk export endpoint for a series, for example `tar.gz`.
- Full generated index export, for example `subtitle-index.jsonl.gz`.
- Optional bulk export by lookup source, such as TVDB.
- Prometheus metrics for export request count, bytes served, and build age.
- Loki-friendly structured logs for rebuilds and fetches.

### Phase III

Consider S3/Garage only if one of these becomes true:

- bulk exports are slow enough to annoy real workflows;
- multiple services need the same subtitle blobs without mounting the mirror;
- corpus jobs need stable object URLs;
- you want lifecycle/versioning independent of the Git mirror;
- you need resumable large downloads outside the LAN service.

If added, object storage should be an optional backend. The generated DB should
store both `repo_path` and optional `object_key`, so local filesystem serving
remains available.

## Title Normalization

Question: should the service normalize the soup of filenames and release titles
using community libraries?

Recommendation: **do light heuristic parsing now, but do not make title
normalization a core dependency in v0.**

The filenames contain useful signal: release groups, platforms, episode numbers,
language tags, `cc`/`sdh`, hashes, resolutions, retime labels, and source names.
They also contain enough chaos that a "normalize everything" pass can become a
project by itself.

For v0, avoid relying on title parsing for identity. Identity should come from
the existing AniList-linked inventory and the crosswalk. Filename parsing should
only produce hints:

- episode candidates;
- language hints;
- leading bracket group;
- release/source tags;
- accessibility tags like `cc` and `sdh`;
- confidence labels.

Community normalization libraries may be worth evaluating later. Candidates to
research include anime release filename parsers and media filename parsers such
as Anitomy-derived tools, GuessIt-style parsers, or Python bindings/wrappers if
they are maintained. But this should be a Phase II/III evaluation, not a v0
foundation.

The better first strategy is series-by-series audit for the messy high-value
cases:

- long-running shows with multiple numbering schemes;
- shows with season-local and absolute numbering;
- filenames with dates before episode numbers;
- preview/special/OVA files mixed with TV episodes;
- files where the same subtitle exists as both `.ass` and `.srt`.

Those audits can harden the parser with concrete examples instead of abstract
normalization ambition.

## Observability

Phase I should log enough to debug rebuilds:

- old and new mirror commits;
- crosswalk source commit used;
- row counts;
- lookup counts;
- rebuild duration;
- smoke-test result;
- API startup DB path and source versions.

Phase II should add proper homelab observability:

- Prometheus `/metrics`.
- Loki-friendly structured logs.
- Dashboard panels for:
  - service health;
  - build age;
  - last successful mirror commit;
  - subtitle row count;
  - lookup row count;
  - request rate by endpoint;
  - file bytes served;
  - rebuild duration;
  - rebuild failures.

## Phases

### Phase I: Useful Local Service

- Add Kitsunekko service package under `envs/services`.
- Add generated SQLite schema and ingestor.
- Use the crosswalk SQLite DB read-only during ingestion.
- Add FastAPI app with health, stats, llms.txt, series lookup, file listing, and
  single-file content fetch.
- Add Docker Compose service and volume.
- Add smoke test for generated DB invariants.

### Phase II: Better Operations and Bulk Use

- Prometheus metrics.
- Loki-friendly structured logs.
- Grafana dashboard design.
- Bulk export endpoints.
- Better parser audit reports.
- Optional `subtitle-index.jsonl.gz` export.

### Phase III: Storage and Ranking

- Evaluate S3/Garage backend if bulk export becomes a real bottleneck.
- Evaluate maintained filename normalization libraries.
- Add subtitle quality/ranking signals.
- Add forced-alignment-derived timing quality if that becomes useful.
- Add corpus mining helpers over exported subtitles.

## Open Questions

- What exact Kitsunekko mirror repository and branch should the service track?
- Should subtitle content be served by `subtitle_id` only, or should stable
  encoded paths also be accepted?
- Should `.ass` and `.srt` duplicates be exposed separately, ranked, or grouped?
- What is the first client workflow: manual curl, Jellyfin/Sonarr helper,
  corpus mining, or ASR benchmark generation?
- Should TVDB broad lookup include both movie and TV matches, matching the
  crosswalk service, or should the Kitsunekko API default to TV-only?

