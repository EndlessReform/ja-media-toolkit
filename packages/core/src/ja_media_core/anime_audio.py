"""Typed SDK for the indexed anime-audio LAN service."""

from __future__ import annotations

import os
import urllib.parse
from typing import Any, Protocol

from ja_media_core.anime_audio_models import (
    AnimeAudioArtifact,
    AnimeAudioEpisode,
    AnimeAudioInventory,
    AnimeAudioInventorySeries,
    AnimeAudioSeries,
    AnimeAudioSubtitle,
    AnimeAudioSubtitleContent,
)
from ja_media_core.http import ServiceHttpClient, ServiceHttpError
from ja_media_core.services import service_base_url

ANIME_AUDIO_BASE_URL_ENV = "ANIME_AUDIO_BASE_URL"
ANIME_AUDIO_GATEWAY_PATH = "/api/v1/audio"


class AnimeAudioNotFoundError(LookupError):
    """The requested indexed anime-audio resource does not exist."""


class AnimeAudioClient(Protocol):
    """Operations consumed by tools that need indexed derived anime audio."""

    def inventory(self) -> AnimeAudioInventory: ...

    def series(self, anilist_id: int) -> AnimeAudioSeries: ...

    def episodes(self, anilist_id: int) -> tuple[AnimeAudioEpisode, ...]: ...

    def artifact(
        self,
        anilist_id: int,
        episode_key: str,
        *,
        profile: str = "portable-aac-v1",
    ) -> AnimeAudioArtifact: ...

    def content(
        self,
        anilist_id: int,
        episode_key: str,
        *,
        profile: str = "portable-aac-v1",
    ) -> bytes: ...

    def subtitles(
        self, anilist_id: int, episode_key: str
    ) -> tuple[AnimeAudioSubtitle, ...]: ...

    def subtitle_content(
        self, anilist_id: int, episode_key: str, subtitle_id: str
    ) -> bytes: ...

    def subtitle_contents(
        self,
        anilist_id: int,
        episode_key: str,
        *,
        subtitle_ids: tuple[str, ...] = (),
        get_all: bool = False,
    ) -> tuple[AnimeAudioSubtitleContent, ...]: ...


class HttpAnimeAudioClient:
    """Synchronous client routed through the shared first-party gateway."""

    def __init__(self, base_url: str | None = None, *, timeout_s: float = 30.0) -> None:
        configured_url = service_base_url(
            base_url,
            (os.environ.get(ANIME_AUDIO_BASE_URL_ENV),),
            ANIME_AUDIO_GATEWAY_PATH,
        )
        if not configured_url:
            raise ValueError(
                "Anime audio base URL is required. Set it via argument, "
                f"{ANIME_AUDIO_BASE_URL_ENV}, or [services].root_url in config.toml"
            )
        self.base_url = configured_url.rstrip("/")
        self._http = ServiceHttpClient(
            self.base_url,
            timeout_s=timeout_s,
            error_label="Anime audio request failed",
        )

    def inventory(self) -> AnimeAudioInventory:
        payload = self._object(self._http.get_json("/inventory"))
        return AnimeAudioInventory.from_mapping(payload)

    def series(self, anilist_id: int) -> AnimeAudioSeries:
        payload = self._object(self._http.get_json(f"/series/{anilist_id}"))
        return AnimeAudioSeries.from_mapping(payload)

    def episodes(self, anilist_id: int) -> tuple[AnimeAudioEpisode, ...]:
        payload = self._http.get_json(f"/series/{anilist_id}/episodes")
        if not isinstance(payload, list):
            raise RuntimeError("Anime audio episodes response was not a list")
        return tuple(AnimeAudioEpisode.from_mapping(item) for item in payload)

    def artifact(
        self,
        anilist_id: int,
        episode_key: str,
        *,
        profile: str = "portable-aac-v1",
    ) -> AnimeAudioArtifact:
        path = self._artifact_path(anilist_id, episode_key, profile)
        try:
            payload = self._http.get_json(path)
        except ServiceHttpError as exc:
            self._raise_not_found(exc, anilist_id, episode_key, profile)
            raise
        return AnimeAudioArtifact.from_mapping(self._object(payload))

    def content(
        self,
        anilist_id: int,
        episode_key: str,
        *,
        profile: str = "portable-aac-v1",
    ) -> bytes:
        path = self._artifact_path(anilist_id, episode_key, profile)
        try:
            return self._http.get_bytes(f"{path}/content")
        except ServiceHttpError as exc:
            self._raise_not_found(exc, anilist_id, episode_key, profile)
            raise

    def subtitles(
        self, anilist_id: int, episode_key: str
    ) -> tuple[AnimeAudioSubtitle, ...]:
        path = self._subtitles_path(anilist_id, episode_key)
        try:
            payload = self._http.get_json(path)
        except ServiceHttpError as exc:
            self._raise_episode_not_found(exc, anilist_id, episode_key)
            raise
        if not isinstance(payload, list):
            raise RuntimeError("Anime audio subtitles response was not a list")
        return tuple(AnimeAudioSubtitle.from_mapping(item) for item in payload)

    def subtitle_content(
        self, anilist_id: int, episode_key: str, subtitle_id: str
    ) -> bytes:
        path = (
            f"{self._subtitles_path(anilist_id, episode_key)}/"
            f"{urllib.parse.quote(subtitle_id, safe='')}/content"
        )
        try:
            return self._http.get_bytes(path)
        except ServiceHttpError as exc:
            self._raise_subtitle_not_found(exc, anilist_id, episode_key, subtitle_id)
            raise

    def subtitle_contents(
        self,
        anilist_id: int,
        episode_key: str,
        *,
        subtitle_ids: tuple[str, ...] = (),
        get_all: bool = False,
    ) -> tuple[AnimeAudioSubtitleContent, ...]:
        query = _subtitle_content_query(subtitle_ids=subtitle_ids, get_all=get_all)
        path = f"{self._subtitles_path(anilist_id, episode_key)}/content{query}"
        try:
            payload = self._http.get_json(path)
        except ServiceHttpError as exc:
            self._raise_episode_not_found(exc, anilist_id, episode_key)
            raise
        if not isinstance(payload, list):
            raise RuntimeError("Anime audio subtitle content response was not a list")
        return tuple(AnimeAudioSubtitleContent.from_mapping(item) for item in payload)

    @staticmethod
    def _artifact_path(anilist_id: int, episode_key: str, profile: str) -> str:
        episode = urllib.parse.quote(episode_key, safe="")
        encoded_profile = urllib.parse.quote(profile, safe="")
        return f"/series/{anilist_id}/episodes/{episode}/artifacts/{encoded_profile}"

    @staticmethod
    def _subtitles_path(anilist_id: int, episode_key: str) -> str:
        episode = urllib.parse.quote(episode_key, safe="")
        return f"/series/{anilist_id}/episodes/{episode}/subtitles"

    @staticmethod
    def _object(payload: object) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise RuntimeError("Anime audio response was not an object")
        return payload

    @staticmethod
    def _raise_not_found(
        exc: ServiceHttpError,
        anilist_id: int,
        episode_key: str,
        profile: str,
    ) -> None:
        if exc.status_code != 404:
            return
        raise AnimeAudioNotFoundError(
            "No derived anime audio artifact for "
            f"AniList {anilist_id}, episode {episode_key!r}, profile {profile!r}"
        ) from exc

    @staticmethod
    def _raise_episode_not_found(
        exc: ServiceHttpError,
        anilist_id: int,
        episode_key: str,
    ) -> None:
        if exc.status_code != 404:
            return
        raise AnimeAudioNotFoundError(
            f"No indexed anime audio episode for AniList {anilist_id}, "
            f"episode {episode_key!r}"
        ) from exc

    @staticmethod
    def _raise_subtitle_not_found(
        exc: ServiceHttpError,
        anilist_id: int,
        episode_key: str,
        subtitle_id: str,
    ) -> None:
        if exc.status_code != 404:
            return
        raise AnimeAudioNotFoundError(
            "No embedded subtitle for "
            f"AniList {anilist_id}, episode {episode_key!r}, "
            f"subtitle {subtitle_id!r}"
        ) from exc


def _subtitle_content_query(
    *,
    subtitle_ids: tuple[str, ...],
    get_all: bool,
) -> str:
    if get_all:
        return "?getall=true"
    if not subtitle_ids:
        raise ValueError("Pass at least one subtitle_id or get_all=True")
    return "?" + urllib.parse.urlencode(
        [("id", subtitle_id) for subtitle_id in subtitle_ids]
    )
