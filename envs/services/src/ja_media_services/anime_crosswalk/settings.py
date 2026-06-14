from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def default_repo_root() -> Path:
    """Return the checkout root for service-local defaults."""

    return Path(__file__).resolve().parents[5]


class AnimeCrosswalkSettings(BaseSettings):
    """Process settings for the anime crosswalk service.

    The DB is generated out of band and opened read-only by the web process.
    ``source_json_path`` is optional; when set, the service can also expose the
    source file as a gzip-capable LAN fileserver endpoint for batch consumers.
    """

    model_config = SettingsConfigDict(env_prefix="ANIME_CROSSWALK_", extra="ignore")

    db_path: Path = Path("/var/lib/anime-crosswalk/anime_lists.sqlite")
    source_json_path: Path | None = None
    repo_root: Path = default_repo_root()
    host: str = "127.0.0.1"
    port: int = 8000
    log_level: str = "INFO"
