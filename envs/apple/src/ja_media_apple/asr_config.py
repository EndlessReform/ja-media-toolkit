from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Annotated, Any, Literal, TypeAlias

from pydantic import ConfigDict, Field

from ja_media_core.asr import AsrBackend
from ja_media_core.config import (
    AsrBackendConfig,
    AsrConfig,
    BackendConfig,
    BackendFactoryRegistry,
    JaMediaConfig,
    load_config,
)


DEFAULT_VIBEVOICE_SYSTEM_PROMPT = (
    "You are a helpful assistant that transcribes audio input into text output "
    "in JSON format."
)
DEFAULT_VIBEVOICE_STOP = ("<|endoftext|>", "<|im_end|>")


class VibeVoiceVllmAsrConfig(AsrBackendConfig):
    """Apple-local VibeVoice encoder with a remote vLLM decoder."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["vibevoice_vllm"] = "vibevoice_vllm"
    vllm_base_url: str
    vllm_model: str
    checkpoint: str = "jkeisling/vibevoice-encoder-only"
    device: str = "cpu"
    dtype: str = "float32"
    system_prompt: str = DEFAULT_VIBEVOICE_SYSTEM_PROMPT
    stop: tuple[str, ...] = DEFAULT_VIBEVOICE_STOP
    seed: int = 0
    transformers_src: str | None = None
    load_on_startup: bool = True
    vad_model_id: str = "mlx-community/silero-vad"
    target_split_s: float | None = 300.0
    split_search_radius_s: float = 45.0
    prefer_split_before_target: bool = False
    rejoin_overlap_s: float = 0.0
    timeout_s: float = 12_000.0
    max_concurrent_requests: int = 1
    max_output_tokens: int | None = 2048
    temperature: float = 0.0
    top_p: float = 1.0
    repetition_penalty: float = 0.1
    backend_options: dict[str, Any] = Field(default_factory=dict)

    def runtime_backend_options(self) -> dict[str, Any]:
        """Return default invocation options for ``AsrRuntimeOptions``."""

        options = dict(self.backend_options)
        options.setdefault("max_concurrent_requests", self.max_concurrent_requests)
        options.setdefault("temperature", self.temperature)
        options.setdefault("top_p", self.top_p)
        options.setdefault("repetition_penalty", self.repetition_penalty)
        if self.max_output_tokens is not None:
            options.setdefault("max_output_tokens", self.max_output_tokens)
        return options


class OpenAiAudioTranscriptionsAsrConfig(AsrBackendConfig):
    """OpenAI-compatible ``/v1/audio/transcriptions`` backend settings."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["openai_audio_transcriptions"] = "openai_audio_transcriptions"
    base_url: str
    model: str
    timeout_s: float = 600.0
    response_format: str = "json"
    temperature: float | None = 0.0
    backend_options: dict[str, Any] = Field(default_factory=dict)

    def runtime_backend_options(self) -> dict[str, Any]:
        """Return default invocation options for ``AsrRuntimeOptions``."""

        options = dict(self.backend_options)
        options.setdefault("response_format", self.response_format)
        if self.temperature is not None:
            options.setdefault("temperature", self.temperature)
        return options


AppleAsrBackendConfig: TypeAlias = Annotated[
    VibeVoiceVllmAsrConfig | OpenAiAudioTranscriptionsAsrConfig,
    Field(discriminator="type"),
]


class AppleAsrConfig(AsrConfig):
    """ASR config re-parsed into backend types importable in ``envs/apple``."""

    backends: dict[str, AppleAsrBackendConfig] = Field(default_factory=dict)

    def get_backend_config(self, name: str | None = None) -> AppleAsrBackendConfig:
        return super().get_backend_config(name)  # type: ignore[return-value]


AsrBackendFactory: TypeAlias = Callable[[AppleAsrBackendConfig], AsrBackend]


def parse_apple_asr_config(config: JaMediaConfig | Mapping[str, Any]) -> AppleAsrConfig:
    """Parse the top-level config's ASR section into Apple-known backends."""

    raw_config = config.model_dump() if isinstance(config, JaMediaConfig) else dict(config)
    raw_asr = raw_config.get("asr", raw_config)
    return AppleAsrConfig.model_validate(raw_asr)


def load_apple_asr_config(
    path: str | Path | None = None,
    *,
    required: bool = False,
) -> AppleAsrConfig:
    """Load and parse ASR config for the Apple runtime."""

    return parse_apple_asr_config(load_config(path, required=required))


def build_asr_backend(
    config: AppleAsrBackendConfig | BackendConfig | Mapping[str, Any],
    *,
    factories: Mapping[str, AsrBackendFactory] | None = None,
) -> AsrBackend:
    """Instantiate one ASR backend from a concrete Apple config entry."""

    parsed_config = _parse_backend_config(config)
    if factories is not None and parsed_config.type in factories:
        return factories[parsed_config.type](parsed_config)

    if isinstance(parsed_config, VibeVoiceVllmAsrConfig):
        from ja_media_apple.asr_vibevoice_vllm import VibeVoiceVllmAsrBackend

        return VibeVoiceVllmAsrBackend(parsed_config)

    raise NotImplementedError(
        f"No Apple ASR backend factory is registered for {parsed_config.type!r}"
    )


def build_selected_asr_backend(
    config: AppleAsrConfig,
    *,
    name: str | None = None,
    factories: Mapping[str, AsrBackendFactory] | None = None,
) -> AsrBackend:
    """Instantiate the selected ASR backend from an Apple ASR config."""

    return build_asr_backend(config.get_backend_config(name), factories=factories)


def _parse_backend_config(
    config: AppleAsrBackendConfig | BackendConfig | Mapping[str, Any],
) -> AppleAsrBackendConfig:
    if isinstance(config, (VibeVoiceVllmAsrConfig, OpenAiAudioTranscriptionsAsrConfig)):
        return config

    registry = _backend_registry()
    return registry.parse_config(config)


def _backend_registry(
    factories: Mapping[str, AsrBackendFactory] | None = None,
) -> BackendFactoryRegistry[AsrBackendConfig, AsrBackend]:
    registry: BackendFactoryRegistry[AsrBackendConfig, AsrBackend] = (
        BackendFactoryRegistry()
    )
    active_factories = _default_factories()
    if factories is not None:
        active_factories.update(factories)
    for backend_type, config_model in {
        "vibevoice_vllm": VibeVoiceVllmAsrConfig,
        "openai_audio_transcriptions": OpenAiAudioTranscriptionsAsrConfig,
    }.items():
        registry.register(
            backend_type,
            config_model,
            active_factories.get(backend_type, _missing_factory(backend_type)),
        )
    return registry


def _default_factories() -> dict[str, AsrBackendFactory]:
    return {}


def _missing_factory(backend_type: str) -> AsrBackendFactory:
    def factory(config: AppleAsrBackendConfig) -> AsrBackend:
        raise NotImplementedError(
            f"No Apple ASR backend factory is registered for {backend_type!r} "
            f"(selected config type: {config.type!r})"
        )

    return factory
