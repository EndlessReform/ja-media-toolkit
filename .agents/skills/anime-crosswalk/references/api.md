# Anime Crosswalk API

Use `ANIME_CROSSWALK_BASE_URL`, with any trailing slash removed:

```sh
base="${ANIME_CROSSWALK_BASE_URL%/}"
```

Never hard-code the VM port in reusable code. For manual testing only, a local
deployment may look like `http://host:58834`.

## Health And Discovery

```sh
curl -fsS "$base/healthz"
curl -fsS "$base/stats"
curl -fsS "$base/llms.txt"
curl -fsS "$base/metrics"
```

`/healthz` includes `ok`, `db_path`, and `source_commit`.

`/stats` includes source metadata and lookup counts.

`/llms.txt` is a Markdown orientation document for non-human consumers.

## Lookup Endpoints

Generic:

```text
GET /resolve/{source}/{id}
GET /resolve/{source}/{media_kind}/{id}
```

Shortcuts:

```text
GET /tvdb/{id}
GET /tvdb/series/{id}
GET /tvdb/movie/{id}
GET /mal/{id}
GET /anidb/{id}
GET /tmdb/tv/{id}
GET /tmdb/movie/{id}
```

Known useful source names include:

```text
anidb
mal
anilist
kitsu
tvdb
tmdb
imdb
anime-planet
anisearch
animenewsnetwork
livechart
simkl
```

`media_kind` is `tv` or `movie`. Use kind-specific lookups for TVDB/TMDB when
the caller knows whether the media is a show or a movie.

## Response Contract

Lookup responses are JSON:

```json
{
  "source": "tvdb",
  "id": "79099",
  "media_kind": "movie",
  "count": 2,
  "results": [
    {
      "anidb_id": 5459,
      "anilist_id": 3269,
      "mal_id": 3269,
      "tvdb_id": 79099,
      "themoviedb_id": {"tv": 8864}
    }
  ]
}
```

Rules:

- `count: 0` with `results: []` is a valid no-match response.
- HTTP `400` means invalid source or media kind.
- `results` may contain more than one row. Do not assume uniqueness.
- IDs in the path can be strings. Preserve IDs from payloads as returned.

## Common Bridge: TVDB To AniList

If a Sonarr record has TVDB ID `79099` and the project needs AniList metadata:

```sh
base="${ANIME_CROSSWALK_BASE_URL%/}"
curl -fsS "$base/tvdb/series/79099"
```

If Sonarr or another source marks the media as a movie:

```sh
curl -fsS "$base/tvdb/movie/79099"
```

Read `results[*].anilist_id`. If multiple rows are returned, preserve all
candidates or choose using additional context such as title/year/season.

## Full JSON Dump

Download the upstream `anime-list-full.json` with gzip transfer:

```sh
curl -fsSL --compressed \
  "$base/data/anime-list-full.json" \
  -o anime-list-full.json
```

The server sends gzip when the client advertises it. `curl --compressed`
requests gzip and writes decompressed JSON to disk.

## DuckDB Coverage Sketch

Use DuckDB when the task is summary stats or coverage analysis:

```sh
duckdb :memory: <<'SQL'
CREATE TABLE anime AS
SELECT * FROM read_json_auto('anime-list-full.json');

SELECT
  count(*) AS rows,
  count(anidb_id) AS anidb_rows,
  count(mal_id) AS mal_rows,
  count(anilist_id) AS anilist_rows,
  count(tvdb_id) AS tvdb_rows,
  count(imdb_id) AS imdb_rows,
  count(themoviedb_id.tv) AS tmdb_tv_rows,
  count(themoviedb_id.movie) AS tmdb_movie_rows
FROM anime;
SQL
```

If DuckDB cannot infer nested fields in the local version, use jq or Python
instead of forcing SQL.
