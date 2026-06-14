# ja-media services

Runnable LAN service environments for `ja-media-toolkit`.

## Anime Crosswalk

Build a SQLite database from a local `Fribb/anime-lists` checkout:

```sh
cd envs/services
uv run anime-crosswalk-ingest \
  --input /srv/anime-lists/anime-list-full.json \
  --output /var/lib/anime-crosswalk/anime_lists.sqlite.next \
  --source-repo Fribb/anime-lists \
  --source-branch master \
  --source-commit "$NEW_SHA"
```

Run the API:

```sh
ANIME_CROSSWALK_DB_PATH=/var/lib/anime-crosswalk/anime_lists.sqlite \
ANIME_CROSSWALK_SOURCE_JSON_PATH=/srv/anime-lists/anime-list-full.json \
uv run anime-crosswalk --host 127.0.0.1 --port 8000
```

Useful endpoints:

- `GET /healthz`
- `GET /stats`
- `GET /metrics`
- `GET /llms.txt`
- `GET /data/anime-list-full.json`
- `GET /resolve/{source}/{id}`
- `GET /resolve/{source}/{media_kind}/{id}`
- `GET /tvdb/{id}`, `/tvdb/series/{id}`, `/tvdb/movie/{id}`
- `GET /mal/{id}`, `/anidb/{id}`, `/tmdb/tv/{id}`, `/tmdb/movie/{id}`

Full JSON file responses are compressed when the client advertises
`Accept-Encoding: gzip`. Use `curl --compressed` to request and decode gzip.
