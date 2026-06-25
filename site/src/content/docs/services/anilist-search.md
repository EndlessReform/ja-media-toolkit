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

**Example Request:**
```sh
curl "http://localhost:8080/api/v1/anilist/search?query=Steins+Gate&k=5"
```

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

### Exact-ID Fallback Cache

Direct AniList fallback is used only by `GET /anime/{anilist_id}` when the ID is
missing from the local CSV index. It is not used for default title search.

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
independently. AniList `429 Retry-After` responses are honored before one retry.

**Python SDK Example:**
```python
from ja_media_core.anilist_search import HttpAniListSearchClient

client = HttpAniListSearchClient()
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
