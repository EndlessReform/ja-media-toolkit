from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

from ja_media_core.crosswalk import normalize_media_kind


KITSUNEKKO_SUBTITLES_BASE_URL_ENV = "KITSUNEKKO_SUBTITLES_BASE_URL"
KITSUNEKKO_SUBTITLES_URL_ENV = "JA_MEDIA_KITSUNEKKO_SUBTITLES_URL"


@dataclass(frozen=True)
class KitsunekkoFileListResponse:
    """Subtitle file-list response returned by the Kitsunekko subtitle service."""

    count: int
    files: tuple[dict[str, Any], ...]
    anilist_id: int | None = None
    source: str | None = None
    external_id: str | None = None
    media_kind: str | None = None
    anilist_ids: tuple[int, ...] = ()
    episode_number: int | None = None

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> KitsunekkoFileListResponse:
        """Parse a file-list JSON payload into a small stable DTO."""

        files = data.get("files", ())
        return cls(
            count=int(data.get("count", len(files))),
            files=tuple(dict(file) for file in files),
            anilist_id=_optional_int(data.get("anilist_id")),
            source=_optional_str(data.get("source")),
            external_id=_optional_str(data.get("id")),
            media_kind=_optional_str(data.get("media_kind")),
            anilist_ids=tuple(int(value) for value in data.get("anilist_ids", ())),
            episode_number=_optional_int(data.get("episode_number")),
        )


@dataclass(frozen=True)
class KitsunekkoStats:
    """Observable service/source metadata returned by ``/stats``."""

    values: dict[str, str]

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> KitsunekkoStats:
        return cls(values={str(key): str(value) for key, value in data.items()})


class KitsunekkoSubtitlesClient(Protocol):
    """Synchronous Kitsunekko subtitle service client contract."""

    def anilist_files(self, anilist_id: int) -> KitsunekkoFileListResponse:
        ...

    def anilist_episode_files(
        self,
        anilist_id: int,
        episode_number: int,
    ) -> KitsunekkoFileListResponse:
        ...

    def anilist_episode_content(
        self,
        anilist_id: int,
        episode_number: int,
        *,
        prefix: str | None = None,
        compression: str = "none",
    ) -> bytes:
        ...

    def tvdb_files(
        self,
        tvdb_id: str | int,
        media_kind: str | None = None,
    ) -> KitsunekkoFileListResponse:
        ...

    def tvdb_episode_files(
        self,
        tvdb_id: str | int,
        episode_number: int,
        media_kind: str | None = None,
    ) -> KitsunekkoFileListResponse:
        ...

    def tvdb_episode_content(
        self,
        tvdb_id: str | int,
        episode_number: int,
        *,
        media_kind: str | None = None,
        prefix: str | None = None,
        compression: str = "none",
    ) -> bytes:
        ...

    def file_metadata(self, file_ref: str) -> dict[str, Any]:
        ...

    def file_content(self, file_ref: str) -> bytes:
        ...

    def stats(self) -> KitsunekkoStats:
        ...

    def health(self) -> dict[str, Any]:
        ...


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _quote(value: str | int) -> str:
    return urllib.parse.quote(str(value), safe="")


def _with_query(path: str, params: dict[str, str | int | None]) -> str:
    clean = {key: value for key, value in params.items() if value is not None}
    if not clean:
        return path
    return f"{path}?{urllib.parse.urlencode(clean)}"


def anilist_files_path(anilist_id: int) -> str:
    """Build the canonical AniList file-list endpoint path."""

    return f"/series/anilist/{_quote(anilist_id)}/files"


def anilist_episode_files_path(anilist_id: int, episode_number: int) -> str:
    """Build the canonical AniList episode file-list endpoint path."""

    return f"/series/anilist/{_quote(anilist_id)}/episodes/{_quote(episode_number)}/files"


def anilist_content_path(
    anilist_id: int,
    *,
    prefix: str | None = None,
    compression: str = "none",
) -> str:
    """Build the canonical AniList series archive endpoint path."""

    return _with_query(
        f"/series/anilist/{_quote(anilist_id)}/content",
        {"prefix": prefix, "compression": compression},
    )


def anilist_episode_content_path(
    anilist_id: int,
    episode_number: int,
    *,
    prefix: str | None = None,
    compression: str = "none",
) -> str:
    """Build the canonical AniList episode archive endpoint path."""

    return _with_query(
        f"/series/anilist/{_quote(anilist_id)}/episodes/{_quote(episode_number)}/content",
        {"prefix": prefix, "compression": compression},
    )


def tvdb_files_path(tvdb_id: str | int, media_kind: str | None = None) -> str:
    """Build the canonical TVDB file-list endpoint path."""

    normalized_kind = normalize_media_kind(media_kind)
    if normalized_kind is None:
        return f"/series/tvdb/{_quote(tvdb_id)}/files"
    return f"/series/tvdb/{_quote(normalized_kind)}/{_quote(tvdb_id)}/files"


def tvdb_episode_files_path(
    tvdb_id: str | int,
    episode_number: int,
    media_kind: str | None = None,
) -> str:
    """Build the canonical TVDB episode file-list endpoint path."""

    normalized_kind = normalize_media_kind(media_kind)
    if normalized_kind is None:
        return f"/series/tvdb/{_quote(tvdb_id)}/episodes/{_quote(episode_number)}/files"
    return (
        f"/series/tvdb/{_quote(normalized_kind)}/{_quote(tvdb_id)}"
        f"/episodes/{_quote(episode_number)}/files"
    )


def tvdb_content_path(
    tvdb_id: str | int,
    media_kind: str | None = None,
    *,
    prefix: str | None = None,
    compression: str = "none",
) -> str:
    """Build the canonical TVDB series archive endpoint path."""

    normalized_kind = normalize_media_kind(media_kind)
    if normalized_kind is None:
        path = f"/series/tvdb/{_quote(tvdb_id)}/content"
    else:
        path = f"/series/tvdb/{_quote(normalized_kind)}/{_quote(tvdb_id)}/content"
    return _with_query(path, {"prefix": prefix, "compression": compression})


def tvdb_episode_content_path(
    tvdb_id: str | int,
    episode_number: int,
    media_kind: str | None = None,
    *,
    prefix: str | None = None,
    compression: str = "none",
) -> str:
    """Build the canonical TVDB episode archive endpoint path."""

    normalized_kind = normalize_media_kind(media_kind)
    if normalized_kind is None:
        path = f"/series/tvdb/{_quote(tvdb_id)}/episodes/{_quote(episode_number)}/content"
    else:
        path = (
            f"/series/tvdb/{_quote(normalized_kind)}/{_quote(tvdb_id)}"
            f"/episodes/{_quote(episode_number)}/content"
        )
    return _with_query(path, {"prefix": prefix, "compression": compression})


def file_metadata_path(file_ref: str) -> str:
    """Build the canonical subtitle metadata endpoint path."""

    return f"/files/{urllib.parse.quote(file_ref, safe='')}"


def file_content_path(file_ref: str) -> str:
    """Build the canonical single-file content endpoint path."""

    return f"{file_metadata_path(file_ref)}/content"


class HttpKitsunekkoSubtitlesClient:
    """Small standard-library HTTP client for the LAN Kitsunekko subtitle service.

    Core keeps this client dependency-light so scripts and package code can
    retrieve subtitle inventories without depending on FastAPI or service
    internals. Episode helpers mirror the service contract: episode numbers are
    local numbers parsed from subtitle filenames at request time.
    """

    def __init__(self, base_url: str | None = None, *, timeout_s: float = 5.0) -> None:
        configured_url = (
            base_url
            or os.environ.get(KITSUNEKKO_SUBTITLES_BASE_URL_ENV)
            or os.environ.get(KITSUNEKKO_SUBTITLES_URL_ENV)
        )
        if not configured_url:
            raise ValueError(
                "Kitsunekko subtitles base URL is required, or set "
                f"{KITSUNEKKO_SUBTITLES_BASE_URL_ENV}"
            )
        self.base_url = configured_url.rstrip("/")
        self.timeout_s = timeout_s

    def anilist_files(self, anilist_id: int) -> KitsunekkoFileListResponse:
        return KitsunekkoFileListResponse.from_mapping(self._get_json(anilist_files_path(anilist_id)))

    def anilist_episode_files(
        self,
        anilist_id: int,
        episode_number: int,
    ) -> KitsunekkoFileListResponse:
        return KitsunekkoFileListResponse.from_mapping(
            self._get_json(anilist_episode_files_path(anilist_id, episode_number))
        )

    def anilist_content(
        self,
        anilist_id: int,
        *,
        prefix: str | None = None,
        compression: str = "none",
    ) -> bytes:
        return self._get_bytes(
            anilist_content_path(anilist_id, prefix=prefix, compression=compression)
        )

    def anilist_episode_content(
        self,
        anilist_id: int,
        episode_number: int,
        *,
        prefix: str | None = None,
        compression: str = "none",
    ) -> bytes:
        return self._get_bytes(
            anilist_episode_content_path(
                anilist_id,
                episode_number,
                prefix=prefix,
                compression=compression,
            )
        )

    def tvdb_files(
        self,
        tvdb_id: str | int,
        media_kind: str | None = None,
    ) -> KitsunekkoFileListResponse:
        return KitsunekkoFileListResponse.from_mapping(
            self._get_json(tvdb_files_path(tvdb_id, media_kind))
        )

    def tvdb_episode_files(
        self,
        tvdb_id: str | int,
        episode_number: int,
        media_kind: str | None = None,
    ) -> KitsunekkoFileListResponse:
        return KitsunekkoFileListResponse.from_mapping(
            self._get_json(tvdb_episode_files_path(tvdb_id, episode_number, media_kind))
        )

    def tvdb_content(
        self,
        tvdb_id: str | int,
        media_kind: str | None = None,
        *,
        prefix: str | None = None,
        compression: str = "none",
    ) -> bytes:
        return self._get_bytes(
            tvdb_content_path(
                tvdb_id,
                media_kind,
                prefix=prefix,
                compression=compression,
            )
        )

    def tvdb_episode_content(
        self,
        tvdb_id: str | int,
        episode_number: int,
        *,
        media_kind: str | None = None,
        prefix: str | None = None,
        compression: str = "none",
    ) -> bytes:
        return self._get_bytes(
            tvdb_episode_content_path(
                tvdb_id,
                episode_number,
                media_kind,
                prefix=prefix,
                compression=compression,
            )
        )

    def tvdb_series_files(self, tvdb_id: str | int) -> KitsunekkoFileListResponse:
        return self.tvdb_files(tvdb_id, media_kind="tv")

    def tvdb_series_episode_files(
        self,
        tvdb_id: str | int,
        episode_number: int,
    ) -> KitsunekkoFileListResponse:
        return self.tvdb_episode_files(tvdb_id, episode_number, media_kind="tv")

    def tvdb_series_episode_content(
        self,
        tvdb_id: str | int,
        episode_number: int,
        *,
        prefix: str | None = None,
        compression: str = "none",
    ) -> bytes:
        return self.tvdb_episode_content(
            tvdb_id,
            episode_number,
            media_kind="tv",
            prefix=prefix,
            compression=compression,
        )

    def file_metadata(self, file_ref: str) -> dict[str, Any]:
        return self._get_json(file_metadata_path(file_ref))

    def file_content(self, file_ref: str) -> bytes:
        return self._get_bytes(file_content_path(file_ref))

    def stats(self) -> KitsunekkoStats:
        return KitsunekkoStats.from_mapping(self._get_json("/stats"))

    def health(self) -> dict[str, Any]:
        return self._get_json("/healthz")

    def _url(self, path: str) -> str:
        return urllib.parse.urljoin(f"{self.base_url}/", path.lstrip("/"))

    def _get_json(self, path: str) -> dict[str, Any]:
        request = urllib.request.Request(self._url(path), headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                return json.loads(response.read().decode(charset))
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Kitsunekko subtitles request failed: {error.code} {body}") from error

    def _get_bytes(self, path: str) -> bytes:
        request = urllib.request.Request(self._url(path), headers={"Accept": "*/*"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Kitsunekko subtitles request failed: {error.code} {body}") from error
