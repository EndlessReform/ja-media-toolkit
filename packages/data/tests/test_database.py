"""Unit tests for database configuration without opening remote connections."""

import pytest

from ja_media_data.database import DATABASE_URL_ENV, database_url_from_env


def test_database_url_is_required(monkeypatch) -> None:
    monkeypatch.delenv(DATABASE_URL_ENV, raising=False)

    with pytest.raises(RuntimeError, match=DATABASE_URL_ENV):
        database_url_from_env()


def test_database_url_requires_psycopg_driver(monkeypatch) -> None:
    monkeypatch.setenv(DATABASE_URL_ENV, "mysql://user:password@db/data")

    with pytest.raises(RuntimeError, match="PostgreSQL"):
        database_url_from_env()


def test_database_url_is_returned_opaquely(monkeypatch) -> None:
    configured = "postgresql+psycopg://user:encoded-password@db/data"
    monkeypatch.setenv(DATABASE_URL_ENV, configured)

    assert database_url_from_env() == configured


def test_standard_postgresql_url_selects_psycopg(monkeypatch) -> None:
    monkeypatch.setenv(
        DATABASE_URL_ENV, "postgresql://user:encoded-password@db/data"
    )

    assert database_url_from_env().startswith("postgresql+psycopg://")
