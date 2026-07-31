"""Typed deployment configuration with TOML defaults and environment overrides."""

from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr
from pydantic_settings import (
    BaseSettings,
    DotEnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)
from dotenv import load_dotenv


CONFIG_PATH_ENV = "JA_MEDIA_DATA_CONFIG"
DEFAULT_CONFIG_PATH = Path(__file__).parents[2] / "config.local.toml"
_SETTING_ROOTS = {
    "environment",
    "bronze",
    "ducklake",
    "control",
    "dagster",
    "services",
    "agent",
}


class _DataDotEnvSource(DotEnvSettingsSource):
    """Ignore framework variables that share the deployment secret file."""

    def __call__(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in super().__call__().items()
            if key in _SETTING_ROOTS
        }


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BronzeSettings(_Section):
    endpoint_url: str
    bucket: str
    prefix: str
    addressing_style: Literal["path", "virtual"] = "path"
    access_key_id: str | None = None
    secret_access_key: str | None = None


class DuckLakeSettings(_Section):
    postgres_url: str
    catalog_schema: str = "ja_media_ducklake"
    data_path: str
    s3_endpoint_url: str | None = None
    s3_region: str = "garage"
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None


class ControlSettings(_Section):
    schema_name: str = Field(default="ja_media_control", alias="schema")


class DagsterSettings(_Section):
    ui_url: str = "http://127.0.0.1:53000"


class ServicesSettings(_Section):
    root_url: str


class AgentSettings(_Section):
    """Server defaults for the optional episode-resolution agent.

    A request may override the model, base URL, and key without mutating these
    process settings. Keys belong in the adjacent dotenv file, never TOML.
    """

    model_id: str | None = None
    base_url: str | None = None
    api_key: SecretStr | None = None
    openai_tracing_enabled: bool = False
    max_turns: int = Field(default=12, ge=1, le=30)
    paused_review_limit: int = Field(default=128, ge=1, le=1024)
    paused_review_ttl_seconds: int = Field(default=1800, ge=60, le=86400)


class DataSettings(BaseSettings):
    """Complete non-framework configuration for one data environment.

    TOML is the stable deployment document. Environment variables use nested
    names such as ``JA_MEDIA_BRONZE__BUCKET`` and override TOML, which is the
    standard pydantic-settings priority model.
    """

    model_config = SettingsConfigDict(
        env_prefix="JA_MEDIA_",
        env_nested_delimiter="__",
        extra="forbid",
        frozen=True,
    )

    environment: Literal["local", "dev", "prod"] = "local"
    bronze: BronzeSettings
    ducklake: DuckLakeSettings
    control: ControlSettings = ControlSettings()
    dagster: DagsterSettings = DagsterSettings()
    services: ServicesSettings
    agent: AgentSettings = AgentSettings()

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        del dotenv_settings
        return (
            init_settings,
            env_settings,
            _DataDotEnvSource(
                settings_cls, env_file=secrets_path(), env_file_encoding="utf-8"
            ),
            TomlConfigSettingsSource(settings_cls, toml_file=config_path()),
            file_secret_settings,
        )


def config_path() -> Path:
    """Return the explicitly selected config or the package-local default."""

    configured = os.environ.get(CONFIG_PATH_ENV)
    return (
        Path(configured).expanduser().resolve() if configured else DEFAULT_CONFIG_PATH
    )


def secrets_path() -> Path:
    """Derive the optional dotenv secret file beside the selected TOML file."""

    path = config_path()
    environment = path.name.removeprefix("config.").removesuffix(".toml")
    return path.with_name(f".env.{environment}")


def load_process_secrets() -> None:
    """Load the selected dotenv for framework clients that read ``os.environ``.

    Pydantic reads the same file without mutating the process environment, but
    Dagster and Celery resolve their connection strings directly from
    environment variables. Existing process values retain precedence.
    """

    load_dotenv(secrets_path(), override=False)


@lru_cache(maxsize=1)
def get_settings() -> DataSettings:
    """Load and validate the process-wide immutable settings object once."""

    path = config_path()
    if not path.is_file():
        raise RuntimeError(
            f"data config not found: {path}; copy config.local.example.toml "
            "to config.local.toml or set JA_MEDIA_DATA_CONFIG"
        )
    return DataSettings()


def reset_settings_cache() -> None:
    """Clear process-local settings state for tests and controlled reconfiguration."""

    get_settings.cache_clear()
