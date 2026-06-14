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

## Docker Compose

The root `compose.yaml` runs the anime crosswalk service on host port `58834`
and container port `8000`. It uses Astral's uv Python image and starts the app
with `uv run`.

From the repo root on the Debian VM:

```sh
docker compose up -d --build
curl http://127.0.0.1:58834/healthz
curl 'http://127.0.0.1:58834/tvdb/movie/79099'
curl --compressed http://127.0.0.1:58834/data/anime-list-full.json >/dev/null
```

The host does not need to clone or mount `Fribb/anime-lists`. The container
owns that implementation detail. It keeps the upstream clone, generated SQLite
DB, and last-ingested source commit in the named Docker volume
`ja-media-services_anime-crosswalk-data`.

On first boot, the container clones `Fribb/anime-lists`, builds
`anime_lists.sqlite`, smoke-tests it, and starts the API. Every 12 hours by
default, an internal updater fetches the upstream branch. If the commit changed,
it builds a `.next` DB, smoke-tests it, atomically swaps it into place, then
terminates PID 1 so Docker's `restart: unless-stopped` policy restarts the API
with a fresh SQLite connection.

To force a full rebuild, remove the named volume:

```sh
docker compose down
docker volume rm ja-media-services_anime-crosswalk-data
docker compose up -d --build
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
