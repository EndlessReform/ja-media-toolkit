"""Typed SDK for the indexed anime-audio LAN service."""

from __future__ import annotations

import os
import urllib.parse
from dataclasses import dataclass
from typing import Any, Protocol

from ja_media_core.http import ServiceHttpClient, ServiceHttpError
from ja_media_core.services import service_base_url

ANIME_AUDIO_BASE_URL_ENV = "ANIME_AUDIO_BASE_URL"
ANIME_AUDIO_GATEWAY_PATH = "/api/v1/audio"


class AnimeAudioNotFoundError(LookupError):
    """The requested indexed anime-audio resource does not exist."""


@dataclass(frozen=True)
class AnimeAudioArtifact:
    """One verified derived-audio artifact exposed by stable identity."""

    anilist_id: int
    episode_key: str
    profile: str
    filename: str
    size_bytes: int
    duration_ms: int
    codec: str
    bitrate_bps: int | None
    channels: int
    sample_rate_hz: int
    sha256: str | None
    created_at: str
    content_url: str

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> AnimeAudioArtifact:
        return cls(**data)


@dataclass(frozen=True)
class AnimeAudioEpisode:
    """One indexed episode and its available profile artifacts."""

    anilist_id: int
    episode_key: str
    artifacts: tuple[AnimeAudioArtifact, ...]

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> AnimeAudioEpisode:
        return cls(
            anilist_id=int(data["anilist_id"]),
            episode_key=str(data["episode_key"]),
            artifacts=tuple(
                AnimeAudioArtifact.from_mapping(item) for item in data.get("artifacts", ())
            ),
        )


@dataclass(frozen=True)
class AnimeAudioSeries:
    """Indexed series summary derived from its authoritative manifest."""

    anilist_id: int
    title: str
    title_english: str | None
    title_native: str | None
    title_romaji: str | None
    profile: str
    episode_count: int
    artifact_count: int

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> AnimeAudioSeries:
        return cls(**data)


@dataclass(frozen=True)
class AnimeAudioInventorySeries:
    """One series entry in a complete inventory projection."""

    anilist_id: int
    title: str
    title_english: str | None
    title_native: str | None
    title_romaji: str | None
    profile: str
    episode_count: int
    artifact_count: int
    episode_keys: tuple[str, ...]
    artifact_profiles: tuple[str, ...]

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> AnimeAudioInventorySeries:
        return cls(
            anilist_id=int(data["anilist_id"]),
            title=str(data["title"]),
            title_english=data.get("title_english"),
            title_native=data.get("title_native"),
            title_romaji=data.get("title_romaji"),
            profile=str(data["profile"]),
            episode_count=int(data["episode_count"]),
            artifact_count=int(data["artifact_count"]),
            episode_keys=tuple(str(key) for key in data.get("episode_keys", ())),
            artifact_profiles=tuple(
                str(profile) for profile in data.get("artifact_profiles", ())
            ),
        )


@dataclass(frozen=True)
class AnimeAudioInventory:
    """Bounded top-level counts plus every indexed series."""

    series_count: int
    episode_count: int
    artifact_count: int
    series: tuple[AnimeAudioInventorySeries, ...]

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> AnimeAudioInventory:
        return cls(
            series_count=int(data["series_count"]),
            episode_count=int(data["episode_count"]),
            artifact_count=int(data["artifact_count"]),
            series=tuple(
                AnimeAudioInventorySeries.from_mapping(item)
                for item in data.get("series", ())
            ),
        )


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

    @staticmethod
    def _artifact_path(anilist_id: int, episode_key: str, profile: str) -> str:
        episode = urllib.parse.quote(episode_key, safe="")
        encoded_profile = urllib.parse.quote(profile, safe="")
        return f"/series/{anilist_id}/episodes/{episode}/artifacts/{encoded_profile}"

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
