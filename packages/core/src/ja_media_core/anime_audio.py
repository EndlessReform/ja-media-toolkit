"""Typed SDK for the indexed anime-audio LAN service."""

from __future__ import annotations

import os
import urllib.parse
from dataclasses import dataclass
from typing import Any, Protocol

from ja_media_core.http import ServiceHttpClient
from ja_media_core.services import service_base_url

ANIME_AUDIO_BASE_URL_ENV = "ANIME_AUDIO_BASE_URL"
ANIME_AUDIO_GATEWAY_PATH = "/api/v1/audio"


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


class AnimeAudioClient(Protocol):
    """Operations consumed by tools that need indexed derived anime audio."""

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
        return AnimeAudioArtifact.from_mapping(self._object(self._http.get_json(path)))

    def content(
        self,
        anilist_id: int,
        episode_key: str,
        *,
        profile: str = "portable-aac-v1",
    ) -> bytes:
        path = self._artifact_path(anilist_id, episode_key, profile)
        return self._http.get_bytes(f"{path}/content")

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
