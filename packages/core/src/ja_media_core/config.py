from __future__ import annotations

import os
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


CONFIG_ENV_VAR = "JA_MEDIA_CONFIG"
APP_CONFIG_DIR_NAME = "ja-media-toolkit"
CONFIG_FILE_NAME = "config.toml"

BackendConfigT = TypeVar("BackendConfigT", bound="BackendConfig")
BackendT = TypeVar("BackendT")


class JaMediaSettings(BaseSettings):
    """Process-level settings that affect config discovery.

    The durable application config lives in TOML. Environment variables are kept
    narrow and are mainly for selecting that file from shell scripts, services,
    or machine-local profiles.
    """

    model_config = SettingsConfigDict(env_prefix="JA_MEDIA_", extra="ignore")

    config: Path | None = None


class BackendConfig(BaseModel):
    """Base model for one configured backend entry.

    Core can parse unknown backend entries without importing environment-specific
    packages. Concrete envs should subclass this with stricter fields and use
    ``type`` as the discriminator.
    """

    model_config = ConfigDict(extra="allow")

    type: str


class BackendGroupConfig(BaseModel):
    """Named backend map plus the currently selected backend."""

    default_backend: str | None = None
    backends: dict[str, BackendConfig] = Field(default_factory=dict)

    def get_backend_config(self, name: str | None = None) -> BackendConfig:
        """Return the explicitly named backend or the configured default."""

        selected_name = name or self.default_backend
        if selected_name is None:
            raise ValueError("No backend name was provided and no default backend is set")
        try:
            return self.backends[selected_name]
        except KeyError as error:
            available = ", ".join(sorted(self.backends)) or "<none>"
            raise KeyError(
                f"Unknown backend {selected_name!r}; available backends: {available}"
            ) from error


class AsrBackendConfig(BackendConfig):
    """Base class for concrete ASR backend config models."""


class AsrConfig(BackendGroupConfig):
    """ASR config envelope shared by all runtime environments."""

    backends: dict[str, AsrBackendConfig] = Field(default_factory=dict)

    def get_backend_config(self, name: str | None = None) -> AsrBackendConfig:
        return super().get_backend_config(name)  # type: ignore[return-value]


class JaMediaConfig(BaseModel):
    """Top-level user config file.

    This model should stay backend-neutral. Environment packages can re-parse
    individual backend maps into their own discriminated unions once they know
    which concrete backend classes are importable in that runtime.
    """

    asr: AsrConfig = Field(default_factory=AsrConfig)


@dataclass
class BackendFactoryRegistry(Generic[BackendConfigT, BackendT]):
    """Parse selected backend config entries and construct backend objects.

    The registry lives in core because the pattern is shared, but concrete envs
    own registration of backend config subclasses and their factories.
    """

    config_models: dict[str, type[BackendConfigT]] = field(default_factory=dict)
    factories: dict[str, Callable[[BackendConfigT], BackendT]] = field(default_factory=dict)

    def register(
        self,
        backend_type: str,
        config_model: type[BackendConfigT],
        factory: Callable[[BackendConfigT], BackendT],
    ) -> None:
        self.config_models[backend_type] = config_model
        self.factories[backend_type] = factory

    def parse_config(self, raw_config: BackendConfig | Mapping[str, Any]) -> BackendConfigT:
        raw_data = (
            raw_config.model_dump()
            if isinstance(raw_config, BackendConfig)
            else dict(raw_config)
        )
        backend_type = raw_data.get("type")
        if not isinstance(backend_type, str):
            raise ValueError("Backend config must include a string 'type' field")
        try:
            config_model = self.config_models[backend_type]
        except KeyError as error:
            available = ", ".join(sorted(self.config_models)) or "<none>"
            raise ValueError(
                f"Unsupported backend type {backend_type!r}; registered types: {available}"
            ) from error
        return config_model.model_validate(raw_data)

    def build(self, raw_config: BackendConfig | Mapping[str, Any]) -> BackendT:
        config = self.parse_config(raw_config)
        return self.factories[config.type](config)

    def build_selected(
        self,
        group_config: BackendGroupConfig,
        *,
        name: str | None = None,
    ) -> BackendT:
        return self.build(group_config.get_backend_config(name))


def xdg_config_home(env: Mapping[str, str] | None = None) -> Path:
    """Return the XDG config home for this process."""

    active_env = os.environ if env is None else env
    configured_home = active_env.get("XDG_CONFIG_HOME")
    if configured_home:
        return Path(configured_home).expanduser()
    return Path.home() / ".config"


def default_config_path(env: Mapping[str, str] | None = None) -> Path:
    """Return the default user config path for ja-media-toolkit."""

    return xdg_config_home(env) / APP_CONFIG_DIR_NAME / CONFIG_FILE_NAME


def resolve_config_path(
    path: str | Path | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> Path:
    """Resolve an explicit path, ``JA_MEDIA_CONFIG``, or the XDG default."""

    if path is not None:
        return Path(path).expanduser()

    active_env = os.environ if env is None else env
    env_path = active_env.get(CONFIG_ENV_VAR)
    if env_path:
        return Path(env_path).expanduser()

    if env is None:
        settings = JaMediaSettings()
        if settings.config is not None:
            return settings.config.expanduser()

    return default_config_path(active_env)


def load_config(
    path: str | Path | None = None,
    *,
    required: bool = False,
    env: Mapping[str, str] | None = None,
) -> JaMediaConfig:
    """Load the ja-media-toolkit TOML config.

    Missing config is treated as an empty config by default so CLIs can still
    work with explicit flags. Pass ``required=True`` for service startup paths
    that must fail fast.
    """

    config_path = resolve_config_path(path, env=env)
    if not config_path.exists():
        if required:
            raise FileNotFoundError(f"Config file does not exist: {config_path}")
        return JaMediaConfig()

    with config_path.open("rb") as config_file:
        data = tomllib.load(config_file)

    return JaMediaConfig.model_validate(data)
