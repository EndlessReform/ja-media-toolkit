---
title: AniList Search Service
description: BM25 fuzzy search service for AniList anime metadata.
---

The AniList Search service provides a high-performance fuzzy search over a local cache of the AniList anime dataset, allowing users to resolve anime titles to AniList IDs without hitting the upstream API for every request.

## API Reference

The service is exposed via the API Gateway at `/api/v1/anilist`.

### Search Anime
`GET /search`

Returns a list of matching anime entries.

**Query Parameters:**

| Parameter | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `query` | `string` | (Required) | The anime title, romaji, or keywords to search for. |
| `k` | `int` | `3` | Number of results to return (1-50). |
| `include_movies` | `bool` | `false` | Include movies in search results. |
| `include_ova` | `bool` | `false` | Include OVA entries in search results. |
| `all_formats` | `bool` | `false` | Include all anime formats (specials, music, etc.). |
| `force_anilist` | `bool` | `false` | Query AniList GraphQL directly instead of the local BM25 index. |

**Example Request:**
```sh
curl "http://localhost:8080/api/v1/anilist/search?query=Steins+Gate&k=5"
```

Forced direct search is opt-in. It is meant for brand-new titles that are not
yet present in the Kaggle-backed local mirror. Direct results are flattened
into the same CSV-shaped contract as exact-ID fallback rows, cached by AniList
ID, and the query result list is cached separately so repeated lookups do not
poll AniList.

```sh
curl "http://localhost:8080/api/v1/anilist/search?query=Class+de+2-banme+ni+Kawaii+Onnanoko+to+Tomodachi+ni+Natta&k=1&force_anilist=true"
```

The current smoke-test expectation for that query is AniList ID `169580`.

### Anime Metadata
`GET /anime/{anilist_id}`

Returns the local AniList CSV row for one anime. This is a broad metadata
endpoint intended for local tooling that needs fields beyond fuzzy search, such
as descriptions, MAL IDs, relations, staff, studios, and character data.

The endpoint is local-first. It checks the Kaggle-backed DuckDB index before
doing anything else. If the exact AniList ID is absent, the service falls back
to AniList GraphQL, flattens the response into the same CSV-shaped metadata
contract, stores that row in DuckDB, and returns it. Later requests for the same
ID are served from the fallback cache until the row expires.

**Query Parameters:**

| Parameter | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `fields` | `string` | All fields | Comma-separated CSV column names to return. |

**Example Requests:**
```sh
curl "http://localhost:8080/api/v1/anilist/anime/395"

curl "http://localhost:8080/api/v1/anilist/anime/395?fields=title_romaji,description,idMal,characters"
```

JSON-like CSV columns such as `characters`, `relations`, `staff`, `studios`,
`synonyms`, `tags`, `rankings`, `externalLinks`, `streamingEpisodes`,
`airingSchedule`, `recommendations`, `reviews`, and score/status distributions
are returned as JSON values when they parse cleanly.

### Direct AniList Fallback Cache

Direct AniList fallback is used by `GET /anime/{anilist_id}` when the ID is
missing from the local CSV index, and by `GET /search` only when
`force_anilist=true`. Default title search remains local BM25 over the DuckDB
mirror.

Fallback rows live in the same DuckDB file as the search index, in tables that
are preserved across CSV index rebuilds. A Kaggle refresh can replace the
derived `anime` table without deleting direct AniList cache rows.

Cache TTLs:

| Row Type | Default TTL | Environment Setting |
| :--- | :--- | :--- |
| `FINISHED` anime | 30 days | `ANILIST_SEARCH_FALLBACK_FINISHED_TTL_SECONDS` |
| Active, unreleased, hiatus, missing, or unknown status | 7 days | `ANILIST_SEARCH_FALLBACK_AIRING_TTL_SECONDS` |
| Negative exact-ID result | 1 day | `ANILIST_SEARCH_FALLBACK_NEGATIVE_TTL_SECONDS` |

When a positive fallback row has expired, the service tries to refresh it from
AniList. If AniList is unavailable, the stale positive row is returned instead
of failing the request. Negative cache rows are not returned as stale data.

Forced search stores two cache layers:

| Cache | Purpose |
| :--- | :--- |
| `anilist_fallback_anime` | CSV-shaped AniList media rows keyed by AniList ID. |
| `anilist_fallback_query` | Direct-search query shapes mapped to ordered result IDs. |

If a forced-search query cache has expired and AniList is unavailable, the
service returns the stale cached ID list when the referenced fallback rows are
still present. This keeps new-title workflows usable during brief upstream
outages without changing normal local search behavior.

### AniList Rate Limit And Debounce

Outbound AniList calls are guarded by `aiolimiter`. The service defaults to 20
GraphQL calls per 60 seconds:

| Setting | Default |
| :--- | :--- |
| `ANILIST_SEARCH_ANILIST_ENDPOINT` | `https://graphql.anilist.co` |
| `ANILIST_SEARCH_ANILIST_RATE_LIMIT_CALLS` | `20` |
| `ANILIST_SEARCH_ANILIST_RATE_LIMIT_PERIOD_SECONDS` | `60` |
| `ANILIST_SEARCH_ANILIST_TIMEOUT_SECONDS` | `15` |

Concurrent misses for the same exact AniList ID are coalesced inside the
process. The first request owns the upstream fetch; matching requests wait for
that task and then read the cached result. Different IDs are allowed to fetch
independently. Forced search is rate-limited and cached, but not coalesced.
AniList `429 Retry-After` responses are honored before one retry.

**Python SDK Example:**
```python
from ja_media_core.anilist_search import HttpAniListSearchClient

client = HttpAniListSearchClient()
results = client.search(
    "Class de 2-banme ni Kawaii Onnanoko to Tomodachi ni Natta",
    top_k=1,
    force_anilist=True,
)
print(results.results[0].anilist_id)

metadata = client.anime(
    395,
    fields=("title_romaji", "description", "characters", "relations"),
)

print(metadata.anilist_id)
print(metadata.get("title_romaji"))

for character in metadata.get("characters", []):
    name = character.get("node", {}).get("name", {})
    print(name.get("native") or name.get("full"))
```

### Health Check
`GET /healthz`

Returns the current health status of the service and the index size.
`GET /health` remains available as a compatibility alias.

**Response Body:**
```json
{
  "status": "ok",
  "rows": 12345,
  "refresh": {
    "last_attempt_unix": 1718880000,
    "last_success_unix": 1718880000,
    "last_failure_unix": null,
    "consecutive_failures": 0,
    "last_index_rows": 12345,
    "stale": false
  }
}
```
