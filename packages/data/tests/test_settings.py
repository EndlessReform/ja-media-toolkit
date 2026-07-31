"""Tests for the pydantic-settings TOML and environment contract."""

import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from ja_media_data.preflight import _safe_error
from ja_media_data.settings import (
    BronzeSettings,
    DataSettings,
    get_settings,
    load_process_secrets,
    reset_settings_cache,
)


def _toml(path: Path) -> None:
    path.write_text(
        """environment = "local"
[bronze]
endpoint_url = "http://garage:3900"
bucket = "bronze"
prefix = "captures/v2"
[ducklake]
postgres_url = "postgresql://local/test"
data_path = "s3://silver/local/"
[services]
root_url = "http://services"
""",
        encoding="utf-8",
    )


def test_environment_overrides_nested_toml_value(tmp_path, monkeypatch) -> None:
    config = tmp_path / "data.toml"
    _toml(config)
    monkeypatch.setenv("JA_MEDIA_DATA_CONFIG", str(config))
    monkeypatch.setenv("JA_MEDIA_BRONZE__BUCKET", "override")
    reset_settings_cache()

    settings = get_settings()

    assert settings.bronze.endpoint_url == "http://garage:3900"
    assert settings.bronze.bucket == "override"


def test_adjacent_dotenv_overrides_toml_but_not_process_env(
    tmp_path, monkeypatch
) -> None:
    config = tmp_path / "config.local.toml"
    _toml(config)
    (tmp_path / ".env.local").write_text(
        "JA_MEDIA_BRONZE__BUCKET=dotenv\nDAGSTER_POSTGRES_URL=postgresql://framework\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("JA_MEDIA_DATA_CONFIG", str(config))
    monkeypatch.setenv("JA_MEDIA_BRONZE__ENDPOINT_URL", "http://process")
    reset_settings_cache()

    settings = get_settings()

    assert settings.bronze.bucket == "dotenv"
    assert settings.bronze.endpoint_url == "http://process"


def test_process_secrets_are_available_to_framework_clients(
    tmp_path, monkeypatch
) -> None:
    config = tmp_path / "config.local.toml"
    _toml(config)
    (tmp_path / ".env.local").write_text(
        "DAGSTER_POSTGRES_URL=postgresql://from-dotenv\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("JA_MEDIA_DATA_CONFIG", str(config))
    monkeypatch.delenv("DAGSTER_POSTGRES_URL", raising=False)

    load_process_secrets()

    assert os.environ["DAGSTER_POSTGRES_URL"] == "postgresql://from-dotenv"


def test_unknown_toml_keys_fail_closed(tmp_path, monkeypatch) -> None:
    config = tmp_path / "data.toml"
    _toml(config)
    config.write_text(config.read_text() + "\nunknown = true\n")
    monkeypatch.setenv("JA_MEDIA_DATA_CONFIG", str(config))
    reset_settings_cache()

    with pytest.raises(ValidationError, match="unknown"):
        get_settings()


def test_bronze_prefix_is_explicit_even_for_a_dedicated_bucket() -> None:
    with pytest.raises(ValidationError, match="prefix"):
        BronzeSettings(endpoint_url="http://garage", bucket="bronze-v2")


def test_settings_are_immutable() -> None:
    settings = DataSettings(
        bronze={
            "endpoint_url": "http://garage",
            "bucket": "bronze-v2",
            "prefix": "captures/v2",
        },
        ducklake={
            "postgres_url": "postgresql://local/test",
            "data_path": "s3://silver/local/",
        },
        services={"root_url": "http://services"},
    )

    with pytest.raises(ValidationError):
        settings.bronze.bucket = "changed"


def test_agent_defaults_and_secret_environment_override(tmp_path, monkeypatch) -> None:
    config = tmp_path / "config.local.toml"
    _toml(config)
    monkeypatch.setenv("JA_MEDIA_DATA_CONFIG", str(config))
    monkeypatch.setenv("JA_MEDIA_AGENT__API_KEY", "test-only-secret")
    reset_settings_cache()

    settings = get_settings()

    assert settings.agent.model_id is None
    assert settings.agent.base_url is None
    assert settings.agent.openai_tracing_enabled is False
    assert settings.agent.max_turns == 12
    assert settings.agent.api_key is not None
    assert settings.agent.api_key.get_secret_value() == "test-only-secret"
    assert "test-only-secret" not in repr(settings)


def test_preflight_errors_redact_url_credentials() -> None:
    error = RuntimeError(
        "cannot reach postgresql://operator:do-not-print@database.example/dev"
    )

    message = _safe_error(error)

    assert "do-not-print" not in message
    assert "postgresql://***:***@database.example/dev" in message
