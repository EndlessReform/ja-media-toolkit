---
title: Services
description: Overview of the backend API services provided by ja-media-toolkit.
---

The ja-media-toolkit includes several backend services that provide programmatic access to anime metadata and subtitles. These services are deployed as containers and are routed through the unified API Gateway.

## Available Services

### Anime Crosswalk
The Anime Crosswalk service creates a RESTful API over Fribb's GitHub-based [anime-lists](https://github.com/Fribb/anime-lists/tree/master) dataset. It allows for resolving and bridging anime IDs across various databases (TVDB, TMDB, MAL, AniDB, AniList, Kitsu, IMDb), which is essential for normalizing media records across different platforms.

- **API Documentation:** [/api/v1/crosswalk/docs](/api/v1/crosswalk/docs)

### Kitsunekko Subtitles
The Kitsunekko Subtitles service provides a local mirror and REST API for accessing Japanese subtitles from the [ajatt-tools Kitsunekko GitHub mirror](https://github.com/Ajatt-Tools/kitsunekko-mirror), facilitating easier mining and alignment.

- **API Documentation:** [/api/v1/subtitles/docs](/api/v1/subtitles/docs)

### AniList Search
The AniList Search service provides BM25 fuzzy search over a local cache of the AniList anime dataset, resolving titles to AniList IDs.

- **API Documentation:** [/api/v1/anilist/search/docs](/services/anilist-search)

