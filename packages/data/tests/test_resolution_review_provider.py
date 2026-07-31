"""Request-scoped model construction without making an API call."""

from agents import OpenAIChatCompletionsModel, OpenAIResponsesModel
from pydantic import SecretStr
import pytest

from ja_media_data.operator.resolution_review.provider import (
    ModelChoice,
    make_run_config,
)
from ja_media_data.settings import AgentSettings


def test_first_party_provider_uses_responses_and_configured_default() -> None:
    config = make_run_config(
        AgentSettings(
            api_key=SecretStr("test-key"),
            model_id="gpt-5.6-terra",
            base_url="https://api.openai.com/v1",
        )
    )

    assert config.model == "gpt-5.6-terra"
    assert config.tracing_disabled is True
    assert config.trace_include_sensitive_data is False
    assert config.model_settings.store is False
    assert isinstance(
        config.model_provider.get_model(config.model), OpenAIResponsesModel
    )


def test_custom_base_uses_chat_completions_and_request_overrides() -> None:
    config = make_run_config(
        AgentSettings(model_id="server-model"),
        ModelChoice(
            model_id="local-model",
            base_url="http://localhost:11434/v1",
        ),
    )

    assert config.model == "local-model"
    assert isinstance(
        config.model_provider.get_model(config.model), OpenAIChatCompletionsModel
    )
    assert config.model_settings is None


def test_openai_tracing_requires_explicit_opt_in_and_remains_redacted() -> None:
    config = make_run_config(
        AgentSettings(
            model_id="trace-model",
            base_url="https://api.openai.com/v1",
            openai_tracing_enabled=True,
        )
    )

    assert config.tracing_disabled is False
    assert config.trace_include_sensitive_data is False


def test_agent_defaults_are_unconfigured_and_tracing_is_off() -> None:
    settings = AgentSettings()

    assert settings.model_id is None
    assert settings.base_url is None
    assert settings.openai_tracing_enabled is False
    with pytest.raises(
        ValueError, match="agent configuration is unset: model_id, base_url"
    ):
        make_run_config(settings)


@pytest.mark.parametrize(
    ("choice", "missing"),
    [
        (ModelChoice(model_id="test-model"), "base_url"),
        (ModelChoice(base_url="http://model.test/v1"), "model_id"),
    ],
)
def test_each_routing_field_is_required(choice: ModelChoice, missing: str) -> None:
    with pytest.raises(ValueError) as error:
        make_run_config(AgentSettings(), choice)

    assert f"configuration is unset: {missing}" in str(error.value)
