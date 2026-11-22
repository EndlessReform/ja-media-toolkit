from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)


DEFAULT_CONFIG_PATH = Path.home() / ".config" / "ja-media-toolkit" / "config.toml"
DEFAULT_GENERATE_MODEL = "gpt-5-mini"


class GenerateSettings(BaseModel):
    """Default generation knobs for SRT cleaning shards."""

    model: str = DEFAULT_GENERATE_MODEL
    window_size: int = 10
    context_cues: int = 0
    flagged_windows_only: bool = False
    max_requests_per_shard: int = 50_000
    max_bytes_per_shard: int = 200 * 1000 * 1000
    single_jsonl: bool = False


class VllmSettings(BaseModel):
    """Default local Docker vLLM offline-batch settings."""

    image: str = "vllm/vllm-openai"
    gpus: str = "all"
    runtime: str = "nvidia"
    model: str = "RedHatAI/gemma-4-26B-A4B-it-NVFP4"
    max_model_len: str = "96000"
    max_num_batched_tokens: str = "16384"
    hf_cache: str = "~/.cache/huggingface"


class ProviderSettings(BaseModel):
    """Default OpenAI-compatible HTTP provider settings."""

    model: str | None = None
    base_url: str | None = None
    api_key_env: str | None = None
    response_format: str = "auto"
    concurrency: int = 4
    timeout: float = 180.0
    request_attempts: int = 8
    repair_attempts: int = 1


class _SrtCleanTomlSource(PydanticBaseSettingsSource):
    """Load `[srt_clean]` from shared config.toml or top-level keys from a dedicated file."""

    def __init__(
        self,
        settings_cls: type[BaseSettings],
        toml_path: Path | None,
    ) -> None:
        super().__init__(settings_cls)
        self._toml_path = toml_path
        self._data = self._load()

    def _load(self) -> dict[str, Any]:
        if self._toml_path is None or not self._toml_path.is_file():
            return {}
        with self._toml_path.open("rb") as handle:
            raw = tomllib.load(handle)
        section = raw.get("srt_clean")
        if isinstance(section, dict):
            return section
        return raw if self._toml_path != DEFAULT_CONFIG_PATH else {}

    def get_field_value(
        self, field: Any, field_name: str
    ) -> tuple[Any, str, bool]:
        return self._data.get(field_name), field_name, False

    def __call__(self) -> dict[str, Any]:
        return dict(self._data)


class SrtCleanSettings(BaseSettings):
    """Configuration for `ja-media-srt-clean` with CLI > env > TOML > defaults."""

    model_config = SettingsConfigDict(
        env_prefix="JA_MEDIA_SRT_CLEAN_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    workspace_root: Path = Path(".ja-media-runs")
    default_backend: str = "vllm"
    generate: GenerateSettings = Field(default_factory=GenerateSettings)
    vllm: VllmSettings = Field(default_factory=VllmSettings)
    provider: ProviderSettings = Field(default_factory=ProviderSettings)

    @classmethod
    def load(
        cls,
        config_path: Path | None = None,
        **overrides: Any,
    ) -> SrtCleanSettings:
        """Load settings from optional TOML path, env vars, and explicit overrides."""

        resolved = config_path.expanduser() if config_path else DEFAULT_CONFIG_PATH
        if config_path is not None and not resolved.is_file():
            raise FileNotFoundError(f"SRT clean config file not found: {resolved}")

        class _BoundSettings(cls):  # type: ignore[valid-type,misc]
            @classmethod
            def settings_customise_sources(
                cls,
                settings_cls: type[BaseSettings],
                init_settings: PydanticBaseSettingsSource,
                env_settings: PydanticBaseSettingsSource,
                dotenv_settings: PydanticBaseSettingsSource,
                file_secret_settings: PydanticBaseSettingsSource,
            ) -> tuple[PydanticBaseSettingsSource, ...]:
                return (
                    init_settings,
                    env_settings,
                    _SrtCleanTomlSource(settings_cls, resolved),
                )

        return _BoundSettings(**overrides)
