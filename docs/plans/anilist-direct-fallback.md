# AniList Direct Fallback Plan

## 1. Problem

`anilist-search` currently treats the Kaggle export
`calebmwelsh/anilist-anime-dataset` as the local source of truth for title
search and `/anime/{anilist_id}` metadata. The checked-out upstream compiler in
`docs/repo-symlinks/Anime-Dataset-Compiler` currently fetches year ranges only
through 2025, so anime starting January 1, 2026 onward can be absent until the
upstream PR is merged and Kaggle is rebuilt.

We need a local fallback that can fetch directly from AniList while protecting
AniList from repeated LAN client polling.

## 2. Goals

1. Keep current local-first behavior by default.
2. Fetch exact AniList IDs directly when the CSV row is absent or an existing
   fallback row has expired.
3. Add an explicit opt-in direct AniList search mode.
4. Cache direct AniList rows in DuckDB with configurable TTLs.
5. Use `aiolimiter`, not a hand-rolled rate limiter.
6. Coalesce duplicate in-process exact-ID fetches.
7. Preserve the current CSV-shaped metadata contract for downstream callers.

## 3. Non-Goals

Do not replace Kaggle as the normal local search source, infer fuzzy fallback
from thresholds or zero-hit heuristics, optimize duplicate in-flight title
searches, add distributed coordination, or perform remote deployment.

## 4. Local Files

- `envs/services/src/ja_media_services/anilist_search/app.py`: add query parameters, exact-ID request coordination, and fallback calls.
- `envs/services/src/ja_media_services/anilist_search/settings.py`: add AniList endpoint, timeout, rate-limit, and TTL config.
- `envs/services/src/ja_media_services/anilist_search/db.py`: keep current CSV
  indexing logic, but avoid growing this file further.
- New focused modules: `anilist_search/anilist_api.py`,
  `anilist_search/fallback_cache.py`, `anilist_search/fallback_schema.py`,
  and `anilist_search/singleflight.py`.
- `envs/services/pyproject.toml`: add `aiolimiter` via `uv add` from
  `envs/services`.
- `packages/core/src/ja_media_core/anilist_search.py`: thread
  `force_anilist`.
- `packages/frontend/src/ja_media_frontend/cli.py`: add
  `ja-media get-id --force-anilist`.
- `packages/frontend/src/ja_media_frontend/anilist_search_cli.py`: pass the new flag to the SDK.
- `site/src/content/docs/services/anilist-search.md`: document fallback, caching, and forced search.

Adjacent cleanup: `envs/services/src/ja_media_services/anilist_search/ingest.py`
imports `ensure_dataset` from `db`, but the function lives in `dataset`.

## 5. Upstream Source To Adapt

Use `docs/repo-symlinks/Anime-Dataset-Compiler/AnimeDatasetCollector/fetch_data.py` as the compatibility source:

- `QUERY`: canonical GraphQL field selection.
- `flatten_anime_data(anime)`: canonical GraphQL-to-CSV row transformer.
- `fetch_anime_page(...)`: reference for POST payload shape and `429
  Retry-After` behavior.
- `convert_to_fuzzy_date(...)`: useful context for the upstream year-range bug,
  but not needed for exact ID or search fallback.

Fallback rows should retain the public column names produced by
`flatten_anime_data()`, especially:

- Identity/title: `id`, `idMal`, `title_romaji`, `title_english`,
  `title_native`, `title_userPreferred`.
- Shape/dates: `type`, `format`, `status`, `episodes`, `duration`, `season`,
  `seasonYear`, `seasonInt`, `startDate_*`, `endDate_*`.
- Search inputs: `title_*`, `synonyms`, `format`, `seasonYear`.
- Rich JSON fields: `genres`, `synonyms`, `tags`, `rankings`, `externalLinks`,
  `streamingEpisodes`, `relations`, `characters`, `staff`, `studios`,
  `airingSchedule`, `recommendations`, `reviews`, `stats_*`.
- Images/links: `siteUrl`, `coverImage_*`, `bannerImage`, `trailer_*`.

Local-only `_fallback_*` provenance fields must not be selectable as normal
metadata fields.

## 6. GraphQL Queries

Define one shared media field fragment/string matching upstream's `QUERY`
selection and run every AniList response through a local port of
`flatten_anime_data()`.

Exact lookup:

```graphql
query ($id: Int!) {
  Media(id: $id, type: ANIME) {
    ...AniListFallbackMediaFields
  }
}
```

Forced search:

```graphql
query ($search: String!, $page: Int!, $perPage: Int!) {
  Page(page: $page, perPage: $perPage) {
    pageInfo { total currentPage lastPage hasNextPage perPage }
    media(type: ANIME, search: $search, sort: SEARCH_MATCH) {
      ...AniListFallbackMediaFields
    }
  }
}
```

Search is direct only when requested via
`GET /search?query=...&force_anilist=true`,
`HttpAniListSearchClient.search(..., force_anilist=True)`, or
`ja-media get-id --force-anilist`. Default `force_anilist=false` keeps current
BM25 behavior. Direct search bypasses BM25, queries AniList, flattens returned
media, and upserts rows into the fallback cache by AniList ID.

## 7. Exact ID Read Flow

1. Try the CSV-backed `anime` table.
2. If found, return the current local result.
3. If absent, read `anilist_fallback_anime`.
4. If the fallback row exists and `expires_at_unix > now`, return it.
5. If absent or expired, enter the per-ID single-flight guard.
6. Fetch AniList under the global `aiolimiter.AsyncLimiter`.
7. Upsert the flattened row with a fresh TTL and return it.
8. If AniList returns no media, write a short negative cache row and return
   `404`.

Cache invalidation is TTL-driven. Do not delete fallback rows merely because Kaggle refreshed, and do not use Kaggle refresh revision as a fallback-validity heuristic.

## 8. Rate Limiting

Add service settings:

- `ANILIST_SEARCH_ANILIST_RATE_LIMIT_CALLS`: default `20`.
- `ANILIST_SEARCH_ANILIST_RATE_LIMIT_PERIOD_SECONDS`: default `60`.
- `ANILIST_SEARCH_ANILIST_TIMEOUT_SECONDS`: default `15`.

Keep one `AsyncLimiter` in app state. Wrap every outbound GraphQL request in
`async with limiter`, honor `429 Retry-After`, record failures in fallback
cache/status metadata, and return stale cached data when available; otherwise
return a controlled `503`.

## 9. In-Process Single-Flight

Add exact-ID request coalescing only. Use
`AppState.inflight_anilist_ids: dict[int, asyncio.Task]` plus an
`asyncio.Lock`. A small FastAPI/ASGI middleware or route dependency recognizes
`/anime/{anilist_id}` fallback fetches; if a task already exists for that ID,
the next request awaits it and then reads the cache. Remove tasks in `finally`.
Different IDs should run independently. Search queries are not coalesced.

## 10. Cache Schema And TTL

Suggested tables:

```sql
CREATE TABLE IF NOT EXISTS anilist_fallback_anime (
  aid VARCHAR PRIMARY KEY,
  payload_json JSON NOT NULL,
  status VARCHAR,
  fetched_at_unix DOUBLE NOT NULL,
  expires_at_unix DOUBLE NOT NULL,
  last_error VARCHAR,
  negative BOOLEAN NOT NULL DEFAULT false
);

CREATE TABLE IF NOT EXISTS anilist_fallback_query (
  cache_key VARCHAR PRIMARY KEY,
  query VARCHAR NOT NULL,
  top_k INTEGER NOT NULL,
  include_movies BOOLEAN NOT NULL,
  include_ova BOOLEAN NOT NULL,
  all_formats BOOLEAN NOT NULL,
  result_ids JSON NOT NULL,
  fetched_at_unix DOUBLE NOT NULL,
  expires_at_unix DOUBLE NOT NULL,
  last_error VARCHAR
);
```

Settings: `ANILIST_SEARCH_FALLBACK_AIRING_TTL_SECONDS` defaults to `604800`
(7 days), `ANILIST_SEARCH_FALLBACK_FINISHED_TTL_SECONDS` defaults to `2592000`
(30 days), and `ANILIST_SEARCH_FALLBACK_NEGATIVE_TTL_SECONDS` defaults to
`86400` (1 day). `FINISHED` uses 30 days. `RELEASING`, `NOT_YET_RELEASED`,
`HIATUS`, missing status, and unknown status use 7 days. Successful direct
fetches always rewrite `fetched_at_unix` and `expires_at_unix`. Expired
fallback rows are fetched on read.

## 11. DuckDB Risks

The current service rebuilds the CSV-derived table in a sibling DB and
atomically publishes it. Fallback writes make this more delicate.

Requirements: startup creates fallback tables idempotently; CSV rebuild
preserves fallback rows; failed rebuild leaves the active DB and fallback cache
intact; field filtering rejects local-only `_fallback_*` fields; JSON columns
parse the same way for CSV and fallback rows; duplicate CSV/fallback IDs never
produce duplicate detail responses.

## 12. API And CLI Contract

`GET /anime/{anilist_id}` needs no required caller change. If provenance is
useful, prefer opt-in `include_provenance=true` so field-filtered responses stay
predictable.

`GET /search` adds:

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `force_anilist` | `bool` | `false` | Query AniList directly instead of local BM25. Results are cached by AniList ID. |

CLI:

```sh
ja-media get-id "Show Name" --force-anilist
ja-media get-id -f "Show.Name.S01E01.mkv" --force-anilist --format json
```

## 13. Observability And Nice-To-Have Hitrate

Minimum counters: exact requests, exact cache hits/misses, exact coalesced
waits, search requests, outbound AniList requests, AniList `429`s, and outbound
AniList errors.

Nice-to-have health fields:

```json
{
  "fallback": {
    "cached_rows": 42,
    "expired_rows": 3,
    "negative_rows": 2,
    "exact_hit_rate": 0.84,
    "search_hit_rate": 0.60,
    "inflight_exact_ids": 1
  }
}
```

Hitrate can start as process-local counters reset on restart.

## 14. Unit Tests

- Schema: representative AniList fixture flattens to CSV-compatible columns.
- Schema: nullable nested objects like `trailer`, `coverImage`, and
  `nextAiringEpisode` do not crash flattening.
- TTL: `FINISHED` uses 30 days; active/unknown statuses use 7 days; negative
  rows use the negative TTL.
- Cache: fresh rows avoid outbound calls; expired rows fetch on read.
- Rate limit: AniList client acquires `AsyncLimiter` once per GraphQL call.
- Errors: `429 Retry-After` and non-200 responses do not corrupt existing
  cached rows.
- Single-flight: concurrent misses for one ID produce one outbound call and
  release the in-flight entry on success or failure.
- DuckDB: rebuild preserves fallback tables; failed rebuild preserves active
  DB; fallback JSON fields parse like CSV JSON fields.
- API: default `/search` stays local; `force_anilist=true` calls AniList.
- SDK/CLI: `force_anilist` is encoded by the SDK and parsed by
  `ja-media get-id`.

## 15. Integration Tests

Use FastAPI `TestClient` or `httpx` against `create_app()` with a temp DuckDB
database and monkeypatched AniList transport. Critical scenarios: exact ID miss
to fetch to cached read to expired fetch; forced search to cached rows to detail
lookup; two concurrent exact misses producing one outbound request; background
Kaggle refresh preserving fallback rows; bad AniList response returning stale
cache when available or a controlled service error.

Focused commands:

```sh
cd envs/services
uv run pytest tests/test_anilist_search_db.py tests/test_anilist_search_refresh.py

uv run pytest packages/core/tests/test_anilist_search.py

cd packages/frontend
uv run pytest tests/test_cli.py
```

## 16. Implementation Order

1. Add `aiolimiter` dependency from `envs/services`.
2. Add settings for endpoint, timeout, rate limit, and TTLs.
3. Add GraphQL client and upstream-compatible schema flattener.
4. Add fallback DuckDB cache helpers.
5. Add exact-ID fetch-on-read fallback.
6. Add exact-ID single-flight guard.
7. Add forced AniList search.
8. Thread `force_anilist` through SDK and CLI.
9. Update service docs and tests.
10. Run focused service, core, and frontend tests.

## 17. File Size Guardrail

`envs/services/src/ja_media_services/anilist_search/db.py` is already close to
the 300-line soft limit. Keep new behavior in focused modules and keep every
touched hand-written file below the 500-line hard limit.
