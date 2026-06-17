---
title: AniList Search Service
description: BM25 fuzzy search service for AniList anime metadata.
---

The AniList Search service provides a high-performance fuzzy search over a local cache of the AniList anime dataset, allowing users to resolve anime titles to AniList IDs without hitting the upstream API for every request.

## API Reference

The service is exposed via the API Gateway at `/api/v1/anilist/search`.

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
curl "http://localhost:8080/api/v1/anilist/search/search?query=Steins+Gate&k=5"
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
