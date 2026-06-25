from __future__ import annotations

import json
from typing import Any


def flatten_anime_data(anime: dict[str, Any]) -> dict[str, Any]:
    """Flatten AniList GraphQL Media data into the service's CSV-shaped row.

    This mirrors the upstream Anime-Dataset-Compiler shape closely enough that
    direct fallback rows can use the same field filtering and JSON decoding as
    rows loaded from the Kaggle CSV export. Nested collections remain JSON
    strings because that is the durable CSV contract downstream callers already
    consume.
    """

    title = anime.get("title") or {}
    start_date = anime.get("startDate") or {}
    end_date = anime.get("endDate") or {}
    trailer = anime.get("trailer") or {}
    cover_image = anime.get("coverImage") or {}
    stats = anime.get("stats") or {}

    return {
        "id": anime.get("id"),
        "idMal": anime.get("idMal"),
        "title_romaji": title.get("romaji"),
        "title_english": title.get("english"),
        "title_native": title.get("native"),
        "title_userPreferred": title.get("userPreferred"),
        "type": anime.get("type"),
        "format": anime.get("format"),
        "status": anime.get("status"),
        "description": anime.get("description"),
        "startDate_year": start_date.get("year"),
        "startDate_month": start_date.get("month"),
        "startDate_day": start_date.get("day"),
        "endDate_year": end_date.get("year"),
        "endDate_month": end_date.get("month"),
        "endDate_day": end_date.get("day"),
        "season": anime.get("season"),
        "seasonYear": anime.get("seasonYear"),
        "seasonInt": anime.get("seasonInt"),
        "episodes": anime.get("episodes"),
        "duration": anime.get("duration"),
        "chapters": anime.get("chapters"),
        "volumes": anime.get("volumes"),
        "countryOfOrigin": anime.get("countryOfOrigin"),
        "isLicensed": anime.get("isLicensed"),
        "source": anime.get("source"),
        "hashtag": anime.get("hashtag"),
        "trailer_id": trailer.get("id"),
        "trailer_site": trailer.get("site"),
        "trailer_thumbnail": trailer.get("thumbnail"),
        "updatedAt": anime.get("updatedAt"),
        "coverImage_extraLarge": cover_image.get("extraLarge"),
        "coverImage_large": cover_image.get("large"),
        "coverImage_medium": cover_image.get("medium"),
        "coverImage_color": cover_image.get("color"),
        "bannerImage": anime.get("bannerImage"),
        "genres": _json(anime.get("genres") or []),
        "synonyms": _json(anime.get("synonyms") or []),
        "tags": _json(anime.get("tags") or []),
        "averageScore": anime.get("averageScore"),
        "meanScore": anime.get("meanScore"),
        "popularity": anime.get("popularity"),
        "favourites": anime.get("favourites"),
        "trending": anime.get("trending"),
        "rankings": _json(anime.get("rankings") or []),
        "isFavourite": anime.get("isFavourite"),
        "isAdult": anime.get("isAdult"),
        "isLocked": anime.get("isLocked"),
        "siteUrl": anime.get("siteUrl"),
        "externalLinks": _json(anime.get("externalLinks") or []),
        "streamingEpisodes": _json(anime.get("streamingEpisodes") or []),
        "relations": _json(_edges(anime, "relations")),
        "characters": _json(_edges(anime, "characters")),
        "staff": _json(_edges(anime, "staff")),
        "studios": _json(_edges(anime, "studios")),
        "nextAiringEpisode": _json_or_none(anime.get("nextAiringEpisode")),
        "airingSchedule": _json(_nodes(anime, "airingSchedule")),
        "recommendations": _json(_edges(anime, "recommendations")),
        "reviews": _json(_edges(anime, "reviews")),
        "stats_scoreDistribution": _json(stats.get("scoreDistribution") or []),
        "stats_statusDistribution": _json(stats.get("statusDistribution") or []),
    }


def _edges(anime: dict[str, Any], key: str) -> list[Any]:
    value = anime.get(key) or {}
    if not isinstance(value, dict):
        return []
    edges = value.get("edges") or []
    return edges if isinstance(edges, list) else []


def _nodes(anime: dict[str, Any], key: str) -> list[Any]:
    value = anime.get(key) or {}
    if not isinstance(value, dict):
        return []
    nodes = value.get("nodes") or []
    return nodes if isinstance(nodes, list) else []


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _json_or_none(value: Any) -> str | None:
    return _json(value) if value else None
