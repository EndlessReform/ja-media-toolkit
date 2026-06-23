"""Runtime settings for the indexed anime-audio service."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class AnimeAudioSettings(BaseSettings):
    """Paths and network settings for one service process."""

    model_config = SettingsConfigDict(env_prefix="ANIME_AUDIO_", extra="ignore")

    library_root: Path = Path("/srv/derived/anime-audio")
    db_path: Path = Path("/var/lib/anime-audio/index.sqlite")
    host: str = "127.0.0.1"
    port: int = 8000
    root_path: str | None = None
    log_level: str = "INFO"
