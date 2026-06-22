---
name: anime-crosswalk
description: Use the ja-media LAN anime metadata crosswalk service or add client code for it. Trigger this skill when resolving IDs across TVDB, TMDB, MAL, AniDB, AniList, Kitsu, IMDb, or related anime metadata sources; when bridging Sonarr/Jellyfin/TVDB records to AniList/MAL/TMDB data; when downloading anime-list-full.json for coverage analysis; or when adding a small HTTP client to another project that should read ANIME_CROSSWALK_BASE_URL.
---

# Anime Crosswalk

## Core Rule

Use `ANIME_CROSSWALK_BASE_URL` as the service base URL. Do not hard-code
hostnames or ports in project code.

When working in `ja-media-toolkit`, `ANIME_CROSSWALK_BASE_URL` is expected to
come from the repo root `.env`. It is the agent's job to load that environment:
source `.env` inside shell commands or use the target project's normal
environment-loading library. Do not ask the user to export it.

Do not read `.env` files directly with content-printing tools such as `cat`,
`sed`, `rg`, or editors. If you only need to check that `.env` exists, use
`stat .env`. If a subprocess needs an env handoff file, write it under `/tmp`
or another gitignored location, do not print secret values, and clean it up
afterward.

Normalize the base URL by removing a trailing slash before appending paths.

## Fast Path

For one-off lookups, use curl:

```sh
base="${ANIME_CROSSWALK_BASE_URL%/}"
curl -fsS "$base/tvdb/movie/79099"
curl -fsS "$base/resolve/anilist/3269"
curl -fsS "$base/tmdb/tv/8864"
curl -fsS "$base/resolve/bulk" \
  -H 'Content-Type: application/json' \
  -d '{"lookups":[{"source":"tvdb","id":"79099","media_kind":"movie"},{"source":"mal","id":"3269"}]}'
```

For scripted lookups, use `scripts/resolve.sh`:

```sh
ANIME_CROSSWALK_BASE_URL=http://host:58834 \
  .agents/skills/anime-crosswalk/scripts/resolve.sh tvdb 79099 movie
```

For the full upstream JSON dump, use gzip transfer:

```sh
ANIME_CROSSWALK_BASE_URL=http://host:58834 \
  .agents/skills/anime-crosswalk/scripts/download-dump.sh /tmp/anime-list-full.json
```

## What To Read

- Read `references/api.md` when using the running service, curl endpoints, response shapes, gzip dump, or DuckDB analysis.
- Read `references/client.md` when adding client code to another project.

## Common Workflows

Bridge Sonarr/TVDB to AniList characters:

1. Get the TVDB ID and whether Sonarr marks it as a series or movie.
2. Query `/tvdb/series/{id}` or `/tvdb/movie/{id}` first; if unsure, query `/tvdb/{id}`.
3. Read `results[*].anilist_id` from the response.
4. Use the AniList ID with the AniList GraphQL API or the target project logic.
5. If `count` is `0`, stop and report no local crosswalk match. Do not invent an ID.

Bulk bridge many IDs:

1. Use `POST /resolve/bulk` with `{"lookups": [...]}` for page-sized batches.
2. Keep each item shaped as `{"source": "...", "id": "...", "media_kind": "tv|movie"}`.
3. Read `results` in order; each entry has the same contract as a single lookup.
4. Treat per-item `count: 0` as a normal no-match result.

Compute metadata coverage:

1. Download `/data/anime-list-full.json` with gzip transfer.
2. Load the JSON into DuckDB, SQLite, jq, or the project language.
3. Count non-null metadata fields such as `mal_id`, `anilist_id`, `tvdb_id`, `imdb_id`, and `themoviedb_id.tv/movie`.

Add client code:

1. Inspect the target project language and style.
2. Prefer a small sync HTTPX client with `trust_env=False` unless the project
   already has another explicit HTTP convention.
3. Read `ANIME_CROSSWALK_BASE_URL` from config/env.
4. Preserve the response contract: `source`, `id`, `media_kind`, `count`, `results`.
5. Treat `count: 0` as a successful no-match response, not an exception.

## Guardrails

- The service is LAN/tailnet oriented and normally unauthenticated.
- Invalid source or media kind returns HTTP `400`.
- No match returns HTTP `200` with `count: 0`.
- Bulk lookup preserves request order and accepts up to 500 lookup items.
- TVDB and TMDB can have TV/movie ambiguity; use kind-specific endpoints when the caller knows the kind.
- Keep copied client code tiny. Do not vendor this whole repo into another project.
