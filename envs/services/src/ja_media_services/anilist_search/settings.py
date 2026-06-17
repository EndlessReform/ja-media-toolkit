from __future__ import annotations

import argparse
import json
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class AniListSearchSettings(BaseSettings):
    """Process settings for the AniList fuzzy-search service."""

    model_config = SettingsConfigDict(env_prefix="ANILIST_SEARCH_", extra="ignore")

    data_dir: Path = Path("/var/lib/anilist-search")
    db_path: Path = Path("/var/lib/anilist-search/anime_index.db")
    host: str = "127.0.0.1"
    port: int = 8000
    root_path: str | None = None
    log_level: str = "INFO"
    update_interval_seconds: int = 3600
