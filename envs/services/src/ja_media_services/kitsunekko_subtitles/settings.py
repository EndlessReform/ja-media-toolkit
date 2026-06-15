from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def default_repo_root() -> Path:
    """Return the checkout root for service-local defaults."""

    return Path(__file__).resolve().parents[5]


class KitsunekkoSubtitlesSettings(BaseSettings):
    """Process settings for the Kitsunekko subtitle service.

    The first implementation only materializes the service database from the
    mounted anime crosswalk database. Kitsunekko mirror settings are present so
    the container contract is stable before the expensive clone/update routine
    is added.
    """

    model_config = SettingsConfigDict(env_prefix="KITSUNEKKO_SUBTITLES_", extra="ignore")

    data_dir: Path = Path("/var/lib/kitsunekko-subtitles")
    db_path: Path = Path("/var/lib/kitsunekko-subtitles/kitsunekko_subtitles.sqlite")
    crosswalk_db_path: Path = Path("/var/lib/anime-crosswalk-ro/anime_lists.sqlite")
    mirror_dir: Path = Path("/var/lib/kitsunekko-subtitles/kitsunekko-mirror")
    upstream_repo_url: str = "git@github.com:Ajatt-Tools/kitsunekko-mirror.git"
    upstream_repo_name: str = "Ajatt-Tools/kitsunekko-mirror"
    upstream_branch: str = "main"
    update_interval_seconds: int = 3600
    update_on_start: bool = True
    repo_root: Path = default_repo_root()
    host: str = "127.0.0.1"
    port: int = 8000
    root_path: str | None = None
    log_level: str = "INFO"
