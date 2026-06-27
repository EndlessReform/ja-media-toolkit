"""AniList normalization and verified cover retrieval."""

from __future__ import annotations

import html
import json
import mimetypes
import os
import re
from datetime import date
from pathlib import Path
from typing import Any

import httpx

from ja_media_core.anilist_search import AnimeMetadata
from ja_media_core.audio_library import AnimeAudioSeriesMetadata, CoverArtifact
from ja_media_core.proc import run as run_process

SELECTED_FIELDS = (
    "title_english",
    "title_native",
    "title_romaji",
    "title_userPreferred",
    "description",
    "format",
    "status",
    "season",
    "seasonYear",
    "episodes",
    "duration",
    "startDate_year",
    "startDate_month",
    "startDate_day",
    "endDate_year",
    "endDate_month",
    "endDate_day",
    "genres",
    "source",
    "countryOfOrigin",
    "coverImage_extraLarge",
    "coverImage_large",
    "coverImage_medium",
    "bannerImage",
    "idMal",
    "siteUrl",
    "updatedAt",
)
MAX_COVER_BYTES = 20 * 1024 * 1024


def normalize_anilist_metadata(metadata: AnimeMetadata) -> AnimeAudioSeriesMetadata:
    """Normalize the CSV-shaped AniList service row into a durable contract."""

    raw = {field: metadata.get(field) for field in SELECTED_FIELDS}
    english = _optional_text(raw["title_english"])
    romaji = _optional_text(raw["title_romaji"])
    native = _optional_text(raw["title_native"])
    user_preferred = _optional_text(raw["title_userPreferred"])
    description_html = _optional_text(raw["description"])
    return AnimeAudioSeriesMetadata(
        anilist_id=metadata.anilist_id,
        title_english=english,
        title_native=native,
        title_romaji=romaji,
        title_preferred=english
        or romaji
        or native
        or user_preferred
        or f"AniList {metadata.anilist_id}",
        description_html=description_html,
        description_text=description_to_plain_text(description_html),
        format=_optional_text(raw["format"]),
        status=_optional_text(raw["status"]),
        season=_optional_text(raw["season"]),
        season_year=_integer_value(raw["seasonYear"]),
        episode_count=_integer_value(raw["episodes"]),
        typical_duration_minutes=_integer_value(raw["duration"]),
        start_date=_date_from_fields(raw, "startDate"),
        end_date=_date_from_fields(raw, "endDate"),
        genres=_normalize_genres(raw["genres"]),
        source=_optional_text(raw["source"]),
        country_of_origin=_optional_text(raw["countryOfOrigin"]),
        cover_url=choose_cover_url(metadata),
        banner_url=_optional_text(raw["bannerImage"]),
        mal_id=_integer_value(raw["idMal"]),
        site_url=_optional_text(raw["siteUrl"]),
        upstream_updated_at=_integer_value(raw["updatedAt"]),
        raw_snapshot=raw,
    )


def description_to_plain_text(description_html: str | None) -> str | None:
    """Convert AniList's small HTML subset to readable plain text."""

    if not description_html:
        return None
    text = re.sub(r"(?i)<br\s*/?>", "\n", description_html)
    text = re.sub(r"(?i)</p\s*>", "\n\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text).replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text or None


def choose_cover_url(metadata: AnimeMetadata) -> str | None:
    """Choose the highest-resolution cover URL exposed by the service."""

    for field in (
        "coverImage_extraLarge",
        "coverImage_large",
        "coverImage_medium",
    ):
        value = _optional_text(metadata.get(field))
        if value:
            return value
    return None


def download_cover(url: str, destination: Path) -> CoverArtifact:
    """Download, size-limit, ffprobe-verify, and atomically publish a cover."""

    temporary = destination.with_name(f".{destination.name}.partial")
    size = 0
    content_type = "application/octet-stream"
    try:
        with (
            httpx.Client(
                timeout=30,
                trust_env=False,
                follow_redirects=True,
            ) as client,
            client.stream(
                "GET",
                url,
                headers={"User-Agent": "ja-media-toolkit/1"},
            ) as response,
        ):
            response.raise_for_status()
            content_type = response.headers.get(
                "content-type", "application/octet-stream"
            ).split(";", 1)[0]
            if (
                not content_type.startswith("image/")
                and content_type != "application/octet-stream"
            ):
                raise ValueError(f"cover response was not an image: {content_type}")
            with temporary.open("wb") as output:
                for chunk in response.iter_bytes(64 * 1024):
                    size += len(chunk)
                    if size > MAX_COVER_BYTES:
                        raise ValueError("cover exceeds the 20 MiB safety limit")
                    output.write(chunk)
        width, height, codec = _probe_cover(temporary)
        media_type = content_type
        if media_type == "application/octet-stream":
            media_type = mimetypes.types_map.get(f".{codec}", "image/jpeg")
        os.replace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return CoverArtifact(
        source_url=url,
        path=destination.name,
        media_type=media_type,
        width=width,
        height=height,
        size_bytes=size,
    )


def _probe_cover(path: Path) -> tuple[int, int, str]:
    result = run_process(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,width,height",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    streams = json.loads(result.stdout).get("streams", [])
    if len(streams) != 1:
        raise ValueError("cover did not contain exactly one image stream")
    stream = streams[0]
    width, height = int(stream.get("width") or 0), int(stream.get("height") or 0)
    if width <= 0 or height <= 0:
        raise ValueError("cover image dimensions are invalid")
    return width, height, str(stream.get("codec_name") or "jpeg")


def _date_from_fields(fields: dict[str, object], prefix: str) -> date | None:
    year = _integer_value(fields.get(f"{prefix}_year"))
    month = _integer_value(fields.get(f"{prefix}_month"))
    day = _integer_value(fields.get(f"{prefix}_day"))
    if year is None or month is None or day is None:
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _integer_value(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() else None


def _normalize_genres(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = [value]
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(text for item in value if (text := _optional_text(item)))


def _optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None
