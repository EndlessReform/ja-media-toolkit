"""Request-scoped OpenAI Agents SDK provider construction."""

from __future__ import annotations

from dataclasses import dataclass
import os

from agents import ModelSettings, OpenAIProvider, RunConfig

from ja_media_data.settings import AgentSettings


@dataclass(frozen=True)
class ModelChoice:
    """Optional per-run overrides supplied by an HTTP, CLI, or TUI adapter."""

    model_id: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    max_turns: int | None = None


def make_run_config(
    settings: AgentSettings,
    choice: ModelChoice | None = None,
) -> RunConfig:
    """Create one provider without retaining a request's ephemeral key.

    The explicit OpenAI API base uses Responses. Other compatible endpoints use
    Chat Completions because Responses compatibility is not assumed.
    """

    choice = choice or ModelChoice()
    # Keep SDK debug logging metadata-only unless an operator deliberately
    # overrides these process flags before startup.
    os.environ.setdefault("OPENAI_AGENTS_DONT_LOG_MODEL_DATA", "1")
    os.environ.setdefault("OPENAI_AGENTS_DONT_LOG_TOOL_DATA", "1")
    model_id = _text(choice.model_id) or _text(settings.model_id)
    base_url = _text(choice.base_url) or _text(settings.base_url)
    missing = [
        name
        for name, value in (("model_id", model_id), ("base_url", base_url))
        if value is None
    ]
    if missing:
        raise ValueError(
            f"agent configuration is unset: {', '.join(missing)}; enter both "
            "model and base URL in the browser or configure [agent]"
        )
    assert model_id is not None and base_url is not None
    configured_key = (
        settings.api_key.get_secret_value() if settings.api_key is not None else None
    )
    api_key = _text(choice.api_key) or configured_key
    custom_provider = base_url.rstrip("/") != "https://api.openai.com/v1"
    provider = OpenAIProvider(
        api_key=api_key or ("not-used" if custom_provider else None),
        base_url=base_url,
        use_responses=not custom_provider,
    )
    return RunConfig(
        model=model_id,
        model_provider=provider,
        # First-party Responses default to retained objects. Custom Chat
        # Completions servers may reject this OpenAI-specific request field.
        model_settings=ModelSettings(store=False) if not custom_provider else None,
        tracing_disabled=not settings.openai_tracing_enabled,
        trace_include_sensitive_data=False,
        workflow_name="episode-resolution-review",
    )


def _text(value: str | None) -> str | None:
    cleaned = value.strip() if value is not None else ""
    return cleaned or None
