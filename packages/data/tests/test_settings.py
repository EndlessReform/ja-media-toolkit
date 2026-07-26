"""Tests for the pydantic-settings TOML and environment contract."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from ja_media_data.preflight import _safe_error
from ja_media_data.settings import DataSettings, get_settings, reset_settings_cache


def _toml(path: Path) -> None:
    path.write_text(
        """environment = "local"
[bronze]
endpoint_url = "http://garage:3900"
bucket = "bronze"
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
        "JA_MEDIA_BRONZE__BUCKET=dotenv\n"
        "DAGSTER_POSTGRES_URL=postgresql://framework\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("JA_MEDIA_DATA_CONFIG", str(config))
    monkeypatch.setenv("JA_MEDIA_BRONZE__ENDPOINT_URL", "http://process")
    reset_settings_cache()

    settings = get_settings()

    assert settings.bronze.bucket == "dotenv"
    assert settings.bronze.endpoint_url == "http://process"


def test_unknown_toml_keys_fail_closed(tmp_path, monkeypatch) -> None:
    config = tmp_path / "data.toml"
    _toml(config)
    config.write_text(config.read_text() + "\nunknown = true\n")
    monkeypatch.setenv("JA_MEDIA_DATA_CONFIG", str(config))
    reset_settings_cache()

    with pytest.raises(ValidationError, match="unknown"):
        get_settings()


def test_settings_are_immutable() -> None:
    settings = DataSettings(
        bronze={"endpoint_url": "http://garage", "bucket": "bronze"},
        ducklake={
            "postgres_url": "postgresql://local/test",
            "data_path": "s3://silver/local/",
        },
        services={"root_url": "http://services"},
    )

    with pytest.raises(ValidationError):
        settings.bronze.bucket = "changed"


def test_preflight_errors_redact_url_credentials() -> None:
    error = RuntimeError(
        "cannot reach postgresql://operator:do-not-print@database.example/dev"
    )

    message = _safe_error(error)

    assert "do-not-print" not in message
    assert "postgresql://***:***@database.example/dev" in message
