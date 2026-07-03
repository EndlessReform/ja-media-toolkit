---
name: anilist-metadata
description: Use for ja-media AniList search/metadata service work: querying or changing envs/services/src/ja_media_services/anilist_search, resolving titles through /api/v1/anilist/search, fetching /anime/{anilist_id} metadata, inspecting available AniList CSV/DuckDB columns, using force_anilist fallback, or testing live AniList metadata/search via the core SDK. Do not use for anime-crosswalk ID bridging.
---

# AniList Metadata Service

## Core Rule

Use this skill for the first-party `anilist_search` service only.

This service owns:

- local AniList title search at `/api/v1/anilist/search`
- exact AniList metadata at `/api/v1/anilist/anime/{anilist_id}`
- direct AniList GraphQL fallback with `force_anilist=true`
- the local DuckDB/Kaggle-backed AniList metadata mirror

Do not route AniList title-search or AniList metadata questions through
`anime_crosswalk`. Crosswalk starts after a source ID is already known; it does
not solve initial AniList candidate disambiguation.

## Source Of Truth

Do not duplicate the API guide in this skill. Read the docsite page when you
need endpoint details, response contracts, cache behavior, or examples:

```text
site/src/content/docs/services/anilist-search.md
```

If the docsite and code disagree, inspect the code and report the mismatch
before changing behavior. The owning service code is:

```text
envs/services/src/ja_media_services/anilist_search/
packages/core/src/ja_media_core/anilist_search.py
```

## Config Discovery And Live Checks

For live/prod/LAN/tailnet checks, use application-level requests only. Do not
SSH, inspect hosts, restart services, deploy, or paste private service URLs into
repo files or final answers.

Prefer the core SDK. It discovers `[services].root_url` from
`~/.config/ja-media-toolkit/config.toml` or `JA_MEDIA_CONFIG`, and accepts the
service override `ANILIST_SEARCH_BASE_URL`.

```sh
PYTHONPATH=packages/core/src uv run python - <<'PY'
from ja_media_core.anilist_search import HttpAniListSearchClient

client = HttpAniListSearchClient()
metadata = client.anime(
    182255,
    fields=(
        "title_romaji",
        "title_english",
        "title_native",
        "format",
        "season",
        "seasonYear",
        "seasonInt",
        "startDate_year",
        "status",
        "relations",
    ),
)

print(metadata.anilist_id)
for key, value in metadata.fields.items():
    print(f"{key}={value!r}")
PY
```

Use live LAN/prod service checks when available. Do not query upstream AniList
directly for analytical work; its GraphQL rate limit is too low for graph
walking. Direct upstream calls are only for tiny sanity checks of our service
behavior, not for bulk metadata discovery or hierarchy reconstruction.

For direct title fallback through our service, keep it explicit:

```sh
PYTHONPATH=packages/core/src uv run python - <<'PY'
from ja_media_core.anilist_search import HttpAniListSearchClient

client = HttpAniListSearchClient()
response = client.search("Sousou no Frieren 2nd Season", top_k=5, force_anilist=True)
for item in response.results:
    print(item.anilist_id, item.title_romaji, item.format, item.season, item.season_year, item.score)
PY
```

If config discovery fails, point to:

```text
site/src/content/docs/setup/config.md
```

## Finding Available Columns

Available metadata fields come from two places:

1. The local `anime` DuckDB table built from the AniList CSV mirror.
2. Fallback rows flattened from AniList GraphQL in `anilist_flatten.py`.

## Root Kaggle Dataset

The service downloads this KaggleHub dataset:

```text
handle: calebmwelsh/anilist-anime-dataset
file: anilist_anime_data_complete.csv
code: envs/services/src/ja_media_services/anilist_search/dataset.py
```

To inspect the root CSV by hand without relying on the service DB:

```sh
cd envs/services
uv run python - <<'PY'
from pathlib import Path
import shutil
import kagglehub

handle = "calebmwelsh/anilist-anime-dataset"
name = "anilist_anime_data_complete.csv"
cached = Path(kagglehub.dataset_download(handle, path=name))
out = Path("/tmp") / name
shutil.copyfile(cached, out)
print(cached)
print(out)
PY
```

Observed on KaggleHub revision `87`, the CSV was
`/tmp/anilist_anime_data_complete.csv`, 436,531,472 bytes, with 20,395 rows and
62 columns:

```text
column00, id, idMal, title_romaji, title_english, title_native,
title_userPreferred, type, format, status, description, startDate_year,
startDate_month, startDate_day, endDate_year, endDate_month, endDate_day,
season, seasonYear, seasonInt, episodes, duration, chapters, volumes,
countryOfOrigin, isLicensed, source, hashtag, trailer_id, trailer_site,
trailer_thumbnail, updatedAt, coverImage_extraLarge, coverImage_large,
coverImage_medium, coverImage_color, bannerImage, genres, synonyms, tags,
averageScore, meanScore, popularity, favourites, trending, rankings,
isFavourite, isAdult, isLocked, siteUrl, externalLinks, streamingEpisodes,
relations, characters, staff, studios, nextAiringEpisode, airingSchedule,
recommendations, reviews, stats_scoreDistribution, stats_statusDistribution
```

The only columns matching root/franchise/series/season/relation/prequel/sequel
style names were:

```text
season
seasonYear
seasonInt
relations
```

There is no root/franchise/mainline season ordinal column in the Kaggle CSV.
Use local `relations` JSON for analytical graph work. Do not walk upstream
AniList live except for small sanity checks.

To list the local table columns from a running service DB, inspect DuckDB:

```sh
cd envs/services
uv run python - <<'PY'
from ja_media_services.anilist_search.db import open_db
from ja_media_services.anilist_search.settings import AniListSearchSettings

settings = AniListSearchSettings()
con = open_db(settings.db_path)
try:
    for row in con.execute("DESCRIBE anime").fetchall():
        print(row[0])
finally:
    con.close()
PY
```

To list fields available from direct AniList fallback, inspect the flattening
contract:

```sh
rg -n '"[^"]+": anime|get\\(".*"\\)|ANILIST_MEDIA_FIELDS' \
  envs/services/src/ja_media_services/anilist_search/anilist_flatten.py \
  envs/services/src/ja_media_services/anilist_search/anilist_api.py
```

Use `GET /anime/{id}?fields=a,b,c` or `client.anime(id, fields=(...))` to probe
specific columns. If you need every returned key for one row:

```sh
PYTHONPATH=packages/core/src uv run python - <<'PY'
from ja_media_core.anilist_search import HttpAniListSearchClient

metadata = HttpAniListSearchClient().anime(182255)
for key in sorted(metadata.fields):
    print(key)
PY
```

Do not invent a field because AniList probably has it. Verify it in one of the
contracts above or from an actual service response.

## Dataset Reality Checks

Kaggle revision `87` contains `154587` (`Sousou no Frieren`) but not `182255`
or `209939`. A service result for `182255` therefore comes from direct AniList
fallback, not the root CSV.

Useful CSV samples from revision `87`:

```json
{"id":154587,"title_romaji":"Sousou no Frieren","title_english":"Frieren: Beyond Journey’s End","format":"TV","season":"FALL","seasonYear":2023,"seasonInt":234,"episodes":28}
{"id":132405,"title_romaji":"Sono Bisque Doll wa Koi wo Suru","title_english":"My Dress-Up Darling","format":"TV","season":"WINTER","seasonYear":2022,"seasonInt":221,"episodes":12}
{"id":154768,"title_romaji":"Sono Bisque Doll wa Koi wo Suru Season 2","title_english":"My Dress-Up Darling Season 2","format":"TV","season":"SUMMER","seasonYear":2025,"seasonInt":253,"episodes":12}
```

`seasonInt` sorts airing seasons: examples above show `221` = winter 2022,
`253` = summer 2025, and `104` = fall 2010. It is not franchise season number.

## GraphQL Boundary

AniList search is a flat `Media` search, not a `Series` search. Schema
introspection shows `Media(search:, sort: SEARCH_MATCH)` plus filters such as
`season`, `seasonYear`, `format_in`, and `id_in`. There is no argument for
franchise root, mainline ordinal, or "season 2 of this named series."

GraphQL can return relation edges on search results, nest relation edges to a
fixed depth, and batch exact `Media(id:)` lookups with aliases. That helps for
bounded probes, but it is not a transitive-closure API and live rate limits make
graph walking the wrong tool.

For hierarchy work:

- prefer the live LAN `anilist_search` service for small service checks;
- use `/tmp/anilist_anime_data_complete.csv` or the service DuckDB for bulk
  analysis;
- parse the CSV `relations` JSON locally;
- only call upstream AniList directly to verify a tiny disputed case or cache
  fill behavior.

## Sample Metadata Payloads

Exact metadata responses are CSV-shaped JSON objects with `anilist_id` plus the
requested fields:

```json
{
  "anilist_id": 182255,
  "title_romaji": "Sousou no Frieren 2nd Season",
  "title_english": "Frieren: Beyond Journey's End Season 2",
  "title_native": "葬送のフリーレン 第2期",
  "format": "TV",
  "season": "WINTER",
  "seasonYear": 2026,
  "seasonInt": 261,
  "startDate_year": 2026,
  "status": "FINISHED",
  "relations": [
    {
      "relationType": "PREQUEL",
      "node": {
        "id": 154587,
        "title": {"romaji": "Sousou no Frieren"},
        "format": "TV"
      }
    }
  ]
}
```

Search responses are lists of candidate summaries:

```json
[
  {
    "anilist_id": 154587,
    "title_english": "Frieren: Beyond Journey's End",
    "title_native": "葬送のフリーレン",
    "title_romaji": "Sousou no Frieren",
    "season": "FALL",
    "season_year": 2023,
    "format": "TV",
    "score": 20.8991
  }
]
```

Important: AniList `season`, `seasonYear`, and `seasonInt` describe airing
season, not franchise season number. Treat `seasonInt` as an airing-season sort
key, not as S1/S2/S3.
