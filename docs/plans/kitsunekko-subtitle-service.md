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

## Current Status

As of the first implementation pass, the service ceremony is in place and has
been exercised end-to-end under Docker Compose / OrbStack:

- `kitsunekko-subtitles` runs beside `anime-crosswalk` in the root Compose
  stack.
- Both services share the `envs/services` image and dispatch to different
  entrypoints. This is enough for now and avoids image drift between the two
  generated-index services.
- The service owns a separate `kitsunekko-subtitles-data` volume containing the
  shallow Git mirror, generated DB, and last-indexed mirror commit.
- The crosswalk SQLite volume is mounted read-only at
  `/var/lib/anime-crosswalk-ro`.
- First clone is intentionally noisy in logs because the repository is large.
  Even as a shallow single-branch clone, the mirror currently lands around 18 GB
  because the live tree itself contains roughly 15 GB of subtitle files and the
  shallow `.git` pack is several GB.
- Incremental updates use `git fetch` and hard reset inside the mirror volume.
- Ingestion scans `.kitsuinfo.json` metadata under the mirror, expands each
  series mapping across discovered subtitle filenames, and batch-writes
  `subtitle_file` rows to SQLite.
- Each subtitle file gets a stable UUIDv5 derived from its mirror-relative
  `repo_path`.
- The DB indexes `subtitle_file(anilist_id)` and
  `subtitle_file(anilist_id, episode_local)`.
- TVDB lookups are resolved at runtime by querying the mounted crosswalk DB and
  then querying `subtitle_file` by AniList ID. We intentionally do not
  denormalize TVDB lookup rows into the Kitsunekko DB.
- Basic `/healthz`, `/stats`, `/metrics`, `/llms.txt`, AniList series/file,
  TVDB series/file, file content, and series content endpoints exist.
- E2E smoke so far: a fresh shallow clone completed, the service indexed about
  122k subtitle rows in a few seconds after clone, and AniList / TVDB file-list
  endpoints returned real subtitle rows.

The next useful step is not more ingestion machinery. It is tightening the API
surface and metrics around the generated index.

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

At Kitsunekko rebuild time, the ingestor reads the crosswalk database read-only
only to resolve `.kitsuinfo.json` metadata into AniList IDs. It then writes one
row per subtitle file into the Kitsunekko DB. The generated subtitle DB should
not materialize one lookup row per external ID per subtitle file; that creates a
large denormalized table and duplicates crosswalk ownership.

At runtime, the API opens the mounted crosswalk DB read-only and performs the
small TVDB-to-AniList resolution query on demand. The request then joins in
application code by querying `subtitle_file` for the resolved AniList IDs. This
keeps the durable boundary clean:

- the crosswalk DB owns external-ID mapping;
- the Kitsunekko DB owns subtitle inventory;
- the runtime API composes the two for convenience.

Store source metadata in the Kitsunekko database:

- Kitsunekko mirror commit.
- Crosswalk source commit.
- build timestamp.
- schema version.
- subtitle row count.
- lookup row count, currently retained as `0` for compatibility with the first
  metrics shape.

The important invariant is that every runtime response can say which source
versions it came from.

## Proposed Schema

Start with a deliberately plain schema:

```sql
CREATE TABLE subtitle_file (
  subtitle_id TEXT PRIMARY KEY,
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

GET /series/anilist/{anilist_id}/content
GET /series/tvdb/{tvdb_id}/content
GET /series/tvdb/{media_kind}/{tvdb_id}/content

GET /files/{subtitle_id-or-name}
GET /files/{subtitle_id-or-name}/content
GET /files/content?ref={subtitle_id}&ref={repo_path}
```

`/series/...` should summarize whether the series exists and what coverage is
available. `/files` should return the concrete subtitle rows. `/content` should
return the subtitle bytes with a useful filename and gzip transfer when the
client accepts it. Multi-file and series content responses should be tar or
tar.gz archives.

Implemented now:

```text
GET /healthz
GET /stats
GET /metrics
GET /llms.txt

GET /series/anilist/{anilist_id}
GET /series/anilist/{anilist_id}/files
GET /series/tvdb/{tvdb_id}
GET /series/tvdb/{tvdb_id}/files
GET /series/tvdb/{media_kind}/{tvdb_id}
GET /series/tvdb/{media_kind}/{tvdb_id}/files

GET /series/anilist/{anilist_id}/content
GET /series/tvdb/{tvdb_id}/content
GET /series/tvdb/{media_kind}/{tvdb_id}/content

GET /files/{subtitle_id-or-name}
GET /files/{subtitle_id-or-name}/content
GET /files/content?ref={subtitle_id}&ref={repo_path}
```

File references resolve in this order:

- UUID `subtitle_id`;
- exact mirror-relative `repo_path`;
- exact `filename`.

Plain filenames are convenient for manual use, but not globally unique. The API
returns an ambiguity error rather than guessing when a filename matches more
than one indexed subtitle row.

Series content endpoints support `prefix`, which filters by mirror-relative
`repo_path` prefix before building the archive. Content endpoints accept
`compression=auto`, `compression=gzip`, or `compression=none`.

Still needed for the v0 API surface:

- Pagination or `limit` / `offset` on file-list endpoints. Some TVDB mappings
  can return many rows, and broad TVDB lookup may map to more than one AniList
  work.
- A clear default for broad TVDB lookup. The current behavior returns every
  AniList ID resolved by the crosswalk; kind-specific TVDB endpoints are
  available when the caller wants `tv` or `movie`.
- Error contracts for missing IDs, unavailable crosswalk DB, and missing mirror
  files.

Potential later additions:

```text
GET /bulk/anilist/{anilist_id}.tar.gz
GET /bulk/tvdb/{tvdb_id}.tar.gz
GET /export/subtitle-index.jsonl.gz
```

Do not add these until the single-file and file-list contracts feel stable.

## Docker Compose

Add a second service next to `anime-crosswalk` using the same `envs/services`
image and a different command/entrypoint path:

```yaml
kitsunekko-subtitles:
  build:
    context: .
    dockerfile: envs/services/Dockerfile
  image: ja-media/services:local
  restart: unless-stopped
  ports:
    - "58835:8000"
  environment:
    KITSUNEKKO_SUBTITLES_HOST: "0.0.0.0"
    KITSUNEKKO_SUBTITLES_PORT: "8000"
    KITSUNEKKO_SUBTITLES_DATA_DIR: "/var/lib/kitsunekko-subtitles"
    KITSUNEKKO_SUBTITLES_DB_PATH: "/var/lib/kitsunekko-subtitles/kitsunekko_subtitles.sqlite"
    KITSUNEKKO_SUBTITLES_UPDATE_INTERVAL_SECONDS: "3600"
    KITSUNEKKO_SUBTITLES_MIRROR_DIR: "/var/lib/kitsunekko-subtitles/kitsunekko-mirror"
    KITSUNEKKO_SUBTITLES_UPSTREAM_REPO_URL: "https://github.com/Ajatt-Tools/kitsunekko-mirror.git"
    KITSUNEKKO_SUBTITLES_UPSTREAM_BRANCH: "main"
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

The shared image is the right current choice. Both `anime-crosswalk` and
`kitsunekko-subtitles` are generated-index FastAPI services with the same Python
environment, same Dockerfile, and different console scripts. Split images only
become worth it if their dependency sets diverge materially.

## Update Flow

The entrypoint should mirror `anime-crosswalk`:

1. Ensure the data directory exists.
2. Clone the Kitsunekko mirror if missing.
3. Fetch/reset the mirror to the configured branch.
4. Compare the current mirror commit with the last indexed commit.
5. If unchanged and a DB exists, keep serving.
6. If changed, build `kitsunekko_subtitles.sqlite.next`.
7. During build, read the crosswalk DB to resolve `.kitsuinfo.json` metadata to
   AniList IDs.
8. Smoke-test the next DB.
9. Move it over the live DB atomically.
10. Restart or reopen the API process.

The timer should default to every 3600 seconds.

The current code follows this shape and also smoke-tests the existing DB when
the mirror commit is unchanged. If the DB is missing or invalid, the service
rebuilds even when the mirror commit has not changed.

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
- Multi-file content archive fetch.
- Series file listing.
- Series content archive fetch with prefix filter.
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

Current observability:

- startup logs include the configured live DB and mounted crosswalk DB;
- mirror clone/fetch/reset logs include repository, branch, and elapsed sync
  time;
- first clone logs warn that it can take several minutes;
- ingest logs progress every 10k rows or after a long scan interval;
- `/stats` exposes DB metadata;
- `/metrics` exists and currently emits DB metadata gauges for subtitle row
  count, lookup row count, and last rebuild success.

Next metrics work:

- add generated DB build age;
- add last rebuild duration once the entrypoint persists it into metadata;
- add mirror commit / crosswalk commit as `info`-style metrics;
- add request counters by route and response status;
- add bytes-served metrics for file and archive content endpoints;
- add rebuild failure counters in a file or metadata handoff that survives API
  restarts.

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

- [x] Add Kitsunekko service package under `envs/services`.
- [x] Add generated SQLite schema and ingestor.
- [x] Use the crosswalk SQLite DB read-only during ingestion.
- [x] Add Docker Compose service and volume.
- [x] Add smoke test for generated DB invariants.
- [x] Add FastAPI app with health, stats, llms.txt, series lookup, and file
  listing.
- [x] Add a basic Prometheus `/metrics` endpoint for generated DB gauges.
- [x] Add single-file metadata lookup.
- [x] Add single-file content fetch.
- [x] Add multi-file content archive fetch by UUID, repo path, or filename.
- [x] Add series content archive fetch with optional prefix filtering.
- [ ] Add pagination / response caps for file-list endpoints.
- [ ] Harden filename episode parsing with real mirror examples.
- [ ] Add API tests for missing IDs, broad TVDB ambiguity, and kind-specific
  TVDB behavior.

### Phase II: Better Operations and Bulk Use

- Expand Prometheus metrics beyond DB gauges.
- Loki-friendly structured logs.
- Grafana dashboard design.
- Bulk export endpoints.
- Better parser audit reports.
- Optional `subtitle-index.jsonl.gz` export.

### Phase III: Storage and Ranking

- Evaluate S3/Garage backend if bulk export becomes a real bottleneck.
- Evaluate maintained filename normalization libraries.

## Open Questions

- Should subtitle content be served by `subtitle_id` only, or should stable
  encoded paths also be accepted?
- Should `.ass` and `.srt` duplicates be exposed separately, ranked, or grouped?
- What is the first client workflow: manual curl, Jellyfin/Sonarr helper,
  corpus mining, or ASR benchmark generation?
- Should TVDB broad lookup include both movie and TV matches, matching the
  crosswalk service, or should the Kitsunekko API default to TV-only?
- Should stale Git lock files be cleaned up automatically after interrupted
  fetches, or should the service fail loudly and wait for operator cleanup?
