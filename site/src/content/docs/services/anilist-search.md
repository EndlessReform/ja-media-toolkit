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

Returns the cached AniList CSV row for one anime. This is a broad metadata
endpoint intended for local tooling that needs fields beyond fuzzy search, such
as descriptions, MAL IDs, relations, staff, studios, and character data.

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
and `synonyms` are returned as JSON values when they parse cleanly.

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
`GET /health`

Returns the current health status of the service and the index size.

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
