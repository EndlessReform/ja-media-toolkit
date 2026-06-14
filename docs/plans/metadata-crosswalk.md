# LAN Anime Metadata Crosswalk Service

Build a small LAN-only metadata service that mirrors
`Fribb/anime-lists`, ingests `anime-list-full.json`, and exposes fast lookup
endpoints for anime metadata ID crosswalks.

The service should live in this monorepo because it will become shared
infrastructure for subtitle search, subtitle timing repair, media renaming,
Jellyfin conventions, Kitsunekko/Jimaku workflows, and future agent tools. The
goal is not just "a thing I can curl"; the goal is a stable local contract that
repo tools can call without every agent rediscovering API paths and response
semantics.

## Goals

- Given an ID from one anime metadata source, return the full crosswalk row from
  `anime-list-full.json`.
- Start with TVDB, MAL, AniDB, and TMDB because those are likely to be most
  useful for media/subtitle tooling.
- Preserve enough source and media-kind information to avoid TVDB/TMDB
  movie-vs-TV ambiguity.
- Provide both:
  - a FastAPI service in `envs/services`; and
  - reusable client/contracts code for other repo tools.
- Keep the implementation simple, boring, local-first, and easy to run in a
  homelab LXC or similar service host.
- Use a generated SQLite DB with atomic replacement instead of a mutable
  application database.
- Provide logs and metrics suitable for Prometheus/Loki-style homelab
  observability.
- Provide a boring full-file endpoint for consumers that need the original
  upstream JSON, with gzip transfer support so batch tools do not waste LAN
  bandwidth.
- Expose `/llms.txt` for non-human consumers. Follow Jeremy Howard's
  `/llms.txt` proposal: a concise Markdown file at a predictable path, with one
  H1, a short blockquote summary, optional explanatory text, H2-delimited link
  lists, and an `Optional` section for secondary context. The file should
  advertise the service API and reproduce the repository README at the bottom
  so an LLM or agent can understand both the service and the broader toolkit.

## Non-Goals

- No Postgres in v0.
- No user authentication.
- No public internet exposure.
- No writable corrections.
- No TMDB/MAL/AniDB API calls.
- No background task queue.
- No collection endpoints in v0.
- No direct joining against Kitsunekko/Jimaku/local subtitle inventory in v0.
- No Jellyfin/Plex/Sonarr integration in v0.

Future services can add inventory joins later. For now, this should be a static
ID resolver with a clean client.

## Package Boundaries

Use the repo's existing split:

```text
packages/core/      durable contracts and lightweight reusable client
envs/services/      runnable FastAPI service, ingestion, updater, systemd
docs/               design notes
```

Do not create a standalone top-level `anime-crosswalk/` project yet. Do not make
a separate repo yet. The crosswalk service is coupled to this repo's media
management workflows, and the monorepo makes it easier for future agents and
tools to depend on one stable local client.

Create a new multirepo project only if this becomes an independently released
public service. Create a separate `packages/crosswalk` only if the client grows
large enough that `packages/core` starts feeling noisy. For v0, the shared
surface is small enough for `packages/core`.

### `packages/core`

Add a module such as:

```text
packages/core/src/ja_media_core/crosswalk.py
```

It should own:

- `AnimeIdSource`
- `MediaKind`
- `CrosswalkLookupRequest`
- `CrosswalkLookupResponse`
- `CrosswalkStats`
- `AnimeCrosswalkClient` protocol
- a small synchronous HTTP client, for example `HttpAnimeCrosswalkClient`
- URL/path construction helpers so callers do not hand-build endpoint strings

Keep this dependency-light. Prefer the Python standard library HTTP stack for
the first client if that avoids adding `httpx` to `packages/core`. If the client
becomes painful or needs async behavior, move concrete HTTP clients into a
separate package later and keep only DTOs/protocols in core.

The client should expose methods like:

```python
client.resolve("tvdb", "79099")
client.resolve("tvdb", "79099", media_kind="movie")
client.tvdb("79099")
client.tvdb_movie("79099")
client.tvdb_series("72025")
client.mal("3269")
client.anidb("5459")
client.tmdb_tv("8864")
client.tmdb_movie("128")
client.stats()
client.health()
```

The key rule: repo code should import a client and pass typed request values,
not construct bare `curl`-style URLs.

### `envs/services`

Create a service environment:

```text
envs/services/
  pyproject.toml
  README.md
  src/
    ja_media_services/
      __init__.py
      anime_crosswalk/
        __init__.py
        app.py
        db.py
        ingest.py
        metrics.py
        settings.py
  scripts/
    update_anime_crosswalk.sh
    smoke_anime_crosswalk.py
  systemd/
    anime-crosswalk.service
    anime-crosswalk-update.service
    anime-crosswalk-update.timer
  tests/
    test_anime_crosswalk_ingest.py
    test_anime_crosswalk_api.py
```

`envs/services` should depend on `ja-media-core` by path plus FastAPI, uvicorn,
Prometheus instrumentation, and any service-only libraries.

Console scripts should be preferred over `python -m` in systemd docs:

```toml
[project.scripts]
anime-crosswalk = "ja_media_services.anime_crosswalk.app:main"
anime-crosswalk-ingest = "ja_media_services.anime_crosswalk.ingest:main"
anime-crosswalk-smoke = "ja_media_services.anime_crosswalk.smoke:main"
```

Run commands from `envs/services`:

```sh
cd envs/services
uv run anime-crosswalk --host 127.0.0.1 --port 8000
uv run anime-crosswalk-ingest \
  --input /srv/anime-lists/anime-list-full.json \
  --output /var/lib/anime-crosswalk/anime_lists.sqlite.next \
  --source-repo Fribb/anime-lists \
  --source-branch master \
  --source-commit "$NEW_SHA"
```

## Upstream Data

Upstream repo: `Fribb/anime-lists`

Relevant generated files:

- `anime-list-full.json`
  - Main merged list.
  - Merged primarily by `anidb_id`.
  - Contains fields like `type`, `anidb_id`, `mal_id`, `anilist_id`,
    `kitsu_id`, `tvdb_id`, `imdb_id`, `themoviedb_id`, `season`, etc.
- `anime-list-mini.json`
  - Same idea, minified.
  - Prefer `anime-list-full.json` for initial ingestion and readability unless
    there is a reason not to.
- `indices/`
  - Upstream precomputed index files.
  - Do not use these as the runtime lookup mechanism. They point to array
    positions in `anime-list-full.json`; build direct lookup rows during
    ingestion instead.
- `collections/`
  - Out of scope for v0 unless trivial to preserve.
  - Do not build collection endpoints until core lookup behavior is clean.

Important upstream caveat: both TheTVDB and TheMovieDB share IDs across movies
and TV shows. Preserve media kind for both sources when the upstream row gives
enough information to do so.

## Architecture

Use:

- Python
- uv
- FastAPI
- SQLite
- systemd service plus systemd timer or a cron-style update script
- Prometheus metrics endpoint
- stdout/stderr logs suitable for Loki

High-level flow:

```text
local git mirror of Fribb/anime-lists
        |
        | scheduled updater: git fetch origin master
        v
if upstream SHA changed:
    reset local mirror to origin/master
    parse anime-list-full.json
    build anime_lists.sqlite.next
    run smoke tests / validation
    atomically replace anime_lists.sqlite
    restart or reload FastAPI service
        |
        v
FastAPI read-only service on tailnet/LAN
        |
        v
ja_media_core.crosswalk.HttpAnimeCrosswalkClient used by local tools
```

Do not use Postgres for v0. The service is a read-heavy static lookup service
with occasional full rebuilds. SQLite is enough and keeps the blast radius
small.

## SQLite Schema

Build a generated SQLite DB. Do not mutate it in place during rebuild. Build
`anime_lists.sqlite.next`, validate it, then replace the live DB file.

Initial schema:

```sql
CREATE TABLE anime (
  row_id INTEGER PRIMARY KEY,
  anidb_id INTEGER,
  payload_json TEXT NOT NULL
);

CREATE TABLE lookup (
  source TEXT NOT NULL,
  external_id TEXT NOT NULL,
  media_kind TEXT,
  row_id INTEGER NOT NULL REFERENCES anime(row_id),
  PRIMARY KEY (source, external_id, media_kind, row_id)
);

CREATE INDEX lookup_source_id_idx
ON lookup(source, external_id);

CREATE INDEX lookup_source_id_kind_idx
ON lookup(source, external_id, media_kind);

CREATE TABLE metadata (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
```

Store the original JSON object in `anime.payload_json`.

Use text for `external_id`, even when the underlying ID is numeric. This avoids
edge cases around IMDb IDs and other string-like identifiers.

### Lookup Mappings

Add lookup rows for any source IDs present in each anime row.

- `anidb_id` -> source `anidb`
- `mal_id` -> source `mal`
- `anilist_id` -> source `anilist`
- `kitsu_id` -> source `kitsu`
- `tvdb_id` -> source `tvdb`
- `imdb_id` -> source `imdb`
- `anime-planet_id` -> source `anime-planet`
- `anisearch_id` -> source `anisearch`
- `animenewsnetwork_id` -> source `animenewsnetwork`
- `livechart_id` -> source `livechart`
- `simkl_id` -> source `simkl`
- `themoviedb_id.tv` -> source `tmdb`, media kind `tv`
- `themoviedb_id.movie` -> source `tmdb`, media kind `movie`

For TVDB, also add kind-aware rows where practical:

- `tvdb_id` with media kind `tv` for series-like rows
- `tvdb_id` with media kind `movie` for movie-like rows

The exact TVDB kind inference should be documented in code because upstream data
is not as explicit for TVDB as it is for TMDB. A conservative first rule is:

- if `type == "MOVIE"` and `season.tvdb` is missing or `0`, add kind `movie`;
- otherwise add kind `tv`;
- also add a kindless `tvdb` row so broad lookups can return all possible
  matches.

If this rule proves wrong for real files, improve the classifier rather than
changing the API shape.

## API Endpoints

Implement these v0 endpoints:

```text
GET /healthz
GET /stats
GET /metrics
GET /llms.txt
GET /data/anime-list-full.json
GET /tvdb/{id}
GET /tvdb/series/{id}
GET /tvdb/movie/{id}
GET /mal/{id}
GET /anidb/{id}
GET /tmdb/tv/{id}
GET /tmdb/movie/{id}
GET /resolve/{source}/{id}
GET /resolve/{source}/{media_kind}/{id}
```

Response shape should always allow multiple results, even when there is usually
one result.

Example:

```json
{
  "source": "tvdb",
  "id": "79099",
  "media_kind": null,
  "count": 1,
  "results": [
    {
      "type": "MOVIE",
      "anidb_id": 5459,
      "mal_id": 3269,
      "tvdb_id": 79099,
      "imdb_id": "tt1164545",
      "themoviedb_id": {
        "tv": 8864
      }
    }
  ]
}
```

If no match:

```json
{
  "source": "tvdb",
  "id": "123456789",
  "media_kind": null,
  "count": 0,
  "results": []
}
```

For invalid sources, return a clear `400`.

For missing IDs, return `200` with empty results. This is lookup semantics, not
a missing-resource read.

### Full Source JSON

Expose the upstream `anime-list-full.json` for clients that need the complete
file rather than lookup responses:

```text
GET /data/anime-list-full.json
```

This endpoint is intentionally plain fileserver behavior. Configure the source
path with `ANIME_CROSSWALK_SOURCE_JSON_PATH`. When the client sends
`Accept-Encoding: gzip`, return the JSON with `Content-Encoding: gzip` and
`Vary: Accept-Encoding`; otherwise return the raw JSON file. This gives agents,
batch scripts, or cache warmers a simple way to fetch the original file without
teaching every consumer where the mirror lives on disk.

### LLM-Facing Discovery

Expose:

```text
GET /llms.txt
```

The response should be Markdown and should be useful if pasted directly into an
LLM context window. Include:

- `# ja-media-toolkit anime crosswalk service`
- a short blockquote summary of what the service does;
- an `## API` section with links and short descriptions for every public
  endpoint;
- an `## Response Contract` section describing multi-result lookup responses,
  no-match semantics, and invalid-input behavior;
- an `## Optional` section that links to the project README context; and
- `## Project README`, followed by the current repository `README.md` content.

This is not a crawler control mechanism and does not replace `robots.txt`,
auth, or access control. It is an inference-time orientation document for LLMs
and agents.

## Client Contract

The service API and the reusable client should use the same semantic contract.
The client should normalize:

- source names, for example `tvdb`, `mal`, `anidb`, `tmdb`;
- external IDs to strings;
- media kind values, for example `tv`, `series`, `movie`;
- no-match responses into `CrosswalkLookupResponse(count=0, results=[])`.

The client should not hide ambiguity. If a broad lookup returns multiple rows,
callers should receive all rows and decide based on season, source filename,
media metadata, or user choice.

Suggested core types:

```python
from dataclasses import dataclass
from typing import Any, Literal, Protocol

AnimeIdSource = Literal[
    "anidb",
    "mal",
    "anilist",
    "kitsu",
    "tvdb",
    "tmdb",
    "imdb",
    "anime-planet",
    "anisearch",
    "animenewsnetwork",
    "livechart",
    "simkl",
]

MediaKind = Literal["tv", "series", "movie"]

@dataclass(frozen=True)
class CrosswalkLookupResponse:
    source: str
    external_id: str
    media_kind: str | None
    count: int
    results: tuple[dict[str, Any], ...]

class AnimeCrosswalkClient(Protocol):
    def resolve(
        self,
        source: str,
        external_id: str | int,
        media_kind: str | None = None,
    ) -> CrosswalkLookupResponse:
        ...
```

The concrete client should support base URLs from config, for example:

```toml
[metadata.crosswalk]
base_url = "http://anime-crosswalk.lan:8000"
timeout_s = 5.0
```

Also support environment variables for simple scripts:

```text
JA_MEDIA_ANIME_CROSSWALK_URL=http://anime-crosswalk.lan:8000
```

## FastAPI Behavior

On startup:

- Open SQLite DB path from config/env.
- Prefer read-only SQLite URI mode where convenient.
- Log startup DB path.
- Log source commit from DB metadata.
- Expose lookups via a simple SQL join between `lookup` and `anime`.

Lookup query:

```sql
SELECT anime.payload_json
FROM lookup
JOIN anime USING (row_id)
WHERE lookup.source = ?
  AND lookup.external_id = ?
  AND (? IS NULL OR lookup.media_kind = ?)
ORDER BY anime.row_id;
```

Be careful with `NULL` media-kind semantics. For non-kind-specific lookups,
match `source + external_id` regardless of kind. For kind-specific TVDB/TMDB
lookups, require `media_kind`.

## Ingestion Behavior

`anime-crosswalk-ingest` should:

1. Read `anime-list-full.json`.
2. Insert each full object into `anime`.
3. Generate zero or more lookup rows per object.
4. Populate metadata.
5. Validate minimum expected conditions.
6. Write to a temp DB path supplied by CLI args.

Suggested CLI:

```sh
uv run anime-crosswalk-ingest \
  --input /srv/anime-lists/anime-list-full.json \
  --output /var/lib/anime-crosswalk/anime_lists.sqlite.next \
  --source-repo Fribb/anime-lists \
  --source-branch master \
  --source-commit "$NEW_SHA"
```

Metadata keys:

- `source_repo`
- `source_branch`
- `source_commit`
- `built_at`
- `anime_count`
- `lookup_count`
- `tvdb_lookup_count`
- `tvdb_tv_lookup_count`
- `tvdb_movie_lookup_count`
- `mal_lookup_count`
- `anidb_lookup_count`
- `tmdb_lookup_count`
- `tmdb_tv_lookup_count`
- `tmdb_movie_lookup_count`
- `schema_version`

Smoke validations:

- DB file exists and can be opened.
- `anime_count > 0`.
- `lookup_count > anime_count`.
- TVDB lookup count is nonzero.
- MAL lookup count is nonzero.
- AniDB lookup count is nonzero.
- TMDB lookup count is nonzero.
- At least one known-looking query works if fixtures are present, but do not
  hardcode brittle upstream assumptions unless necessary.

## Updater

Create `envs/services/scripts/update_anime_crosswalk.sh`.

Behavior:

```sh
#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="/srv/anime-lists"
DATA_DIR="/var/lib/anime-crosswalk"
DB_PATH="$DATA_DIR/anime_lists.sqlite"
NEXT_DB_PATH="$DATA_DIR/anime_lists.sqlite.next"
BRANCH="master"

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

cd /opt/ja-media-toolkit/envs/services
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
```

Use a systemd timer every 12 hours. Pick a non-round minute, for example 03:17
and 15:17 local time.

Downtime is acceptable, so restarting the service after DB replacement is fine.

## Config

Service environment variables:

```text
ANIME_CROSSWALK_DB_PATH=/var/lib/anime-crosswalk/anime_lists.sqlite
ANIME_CROSSWALK_SOURCE_JSON_PATH=/srv/anime-lists/anime-list-full.json
ANIME_CROSSWALK_HOST=0.0.0.0
ANIME_CROSSWALK_PORT=8000
ANIME_CROSSWALK_LOG_LEVEL=INFO
```

Client config should integrate with the repo's existing TOML settings pattern
where possible:

```toml
[metadata.crosswalk]
base_url = "http://anime-crosswalk.lan:8000"
timeout_s = 5.0
```

## Observability

Logs:

- Log startup DB path.
- Log source commit from metadata.
- Log lookup errors.
- In the updater, log old SHA, new SHA, row counts, lookup counts, build
  duration, and success/failure.

Metrics:

- Expose `/metrics`.
- Add standard FastAPI request count/latency if easy.
- Add custom gauges:
  - `anime_crosswalk_rows_total`
  - `anime_crosswalk_lookups_total`
  - `anime_crosswalk_lookups_total{source="tvdb"}`
  - `anime_crosswalk_lookups_total{source="mal"}`
  - `anime_crosswalk_lookups_total{source="anidb"}`
  - `anime_crosswalk_lookups_total{source="tmdb"}`
  - `anime_crosswalk_last_rebuild_success`
  - `anime_crosswalk_source_commit_info` if practical, otherwise expose commit
    via `/stats`.

`/stats` should return metadata from the DB:

```json
{
  "source_repo": "Fribb/anime-lists",
  "source_branch": "master",
  "source_commit": "...",
  "built_at": "...",
  "anime_count": 12345,
  "lookup_count": 67890,
  "schema_version": "1"
}
```

## Current Progress

Implemented in this repo:

- `packages/core/src/ja_media_core/crosswalk.py` with typed lookup DTOs,
  normalization helpers, URL path construction, `AnimeCrosswalkClient`, and a
  standard-library `HttpAnimeCrosswalkClient`.
- `packages/core/src/ja_media_core/config.py` now has
  `[metadata.crosswalk]` config fields for `base_url` and `timeout_s`.
- `envs/services` exists as a runnable uv environment with FastAPI, uvicorn,
  Prometheus client, path dependency on `ja-media-core`, console scripts, and a
  lockfile.
- `envs/services/src/ja_media_services/anime_crosswalk/ingest.py` builds a
  generated SQLite DB from `anime-list-full.json`, including TVDB/MAL/AniDB/TMDB
  lookup rows and metadata.
- `envs/services/src/ja_media_services/anime_crosswalk/app.py` exposes
  `/healthz`, `/stats`, `/metrics`, `/llms.txt`,
  `/data/anime-list-full.json`, broad resolve endpoints, kind-specific resolve
  endpoints, and convenience TVDB/MAL/AniDB/TMDB routes.
- `/data/anime-list-full.json` supports gzip transfer when the client advertises
  `Accept-Encoding: gzip`.
- `/llms.txt` advertises the API and appends the repository README.
- Systemd unit/timer files and an update script are scaffolded under
  `envs/services/systemd` and `envs/services/scripts`.
- Tests cover core URL/response contracts, ingestion, lookup API behavior,
  empty-match semantics, invalid-source handling, gzip negotiation, and
  `llms.txt` README inclusion.

Verified from `envs/services`:

```sh
uv run pytest ../../packages/core/tests tests
```

Result: 25 passed.

Real-data smoke against the local `docs/repo-symlinks/anime-lists` clone:

- Built `envs/services/data/anime_lists.sqlite` from
  `docs/repo-symlinks/anime-lists/anime-list-full.json`.
- Source commit:
  `a534cf66bb5850ce7df964193e14fc014deca589`.
- Smoke metadata: 42,151 anime rows and 214,296 projected lookup rows.
- Started the service on `http://127.0.0.1:8766` after `8765` was already in
  use.
- Verified `/healthz`, `/stats`, `/tvdb/movie/79099`, `/mal/3269`,
  `/tmdb/tv/8864`, no-match lookup semantics, invalid-source `400`,
  `/metrics`, gzip headers for `/data/anime-list-full.json`, and `/llms.txt`.
- Real TVDB/TMDB examples returned multiple rows, which confirms the
  multi-result response shape is necessary rather than theoretical.

## Future Direction

Possible next steps after v0:

- Add local subtitle inventory tables.
- Add Kitsunekko/Jimaku search adapters that use the core crosswalk client.
- Add Jellyfin/Plex/Sonarr helpers for filename and metadata reconciliation.
- Mirror the SQLite schema into Postgres only if inventory queries start needing
  richer joins or long-lived writable state.
- Split `ja_media_core.crosswalk` into `packages/crosswalk` if the client grows
  beyond simple DTOs and HTTP lookup helpers.

## Acceptance Criteria

The project is done when:

1. `cd envs/services && uv run anime-crosswalk` starts the FastAPI app locally.
2. Ingestion builds a SQLite DB from `anime-list-full.json`.
3. `/healthz` returns OK.
4. `/stats` returns source commit and row counts.
5. `/tvdb/{id}` works for IDs present in the DB.
6. `/tvdb/series/{id}` and `/tvdb/movie/{id}` use media-kind-specific lookup.
7. `/mal/{id}` works for IDs present in the DB.
8. `/anidb/{id}` works for IDs present in the DB.
9. `/tmdb/tv/{id}` and `/tmdb/movie/{id}` respect TMDB media kind.
10. `ja_media_core.crosswalk.HttpAnimeCrosswalkClient` can call the service and
    parse health, stats, no-match, single-match, and multi-match responses.
11. Tests cover client URL construction and response parsing without needing a
    live server.
12. Update script fetches upstream, compares SHA, rebuilds only on change, swaps
    DB, and restarts the service.
13. Prometheus metrics endpoint exists.
14. `/data/anime-list-full.json` serves the source JSON and supports gzip
    transfer.
15. `/llms.txt` advertises API methods and reproduces the project README for
    LLM/agent consumers.
16. Logs are useful enough for Loki.
17. README explains install, build, run, update, client config, and systemd
    timer setup.

## Implementation Preference

Favor clarity over cleverness. This is boring infrastructure: static-ish
upstream JSON, generated local SQLite, read-only FastAPI service, scheduled
refresh, and a small typed client so humans and agents do not have to remember
endpoint trivia.
