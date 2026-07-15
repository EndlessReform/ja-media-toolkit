"""PostgreSQL connection and session construction for the domain ledger."""

from __future__ import annotations

import os

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker


DATABASE_URL_ENV = "JA_MEDIA_DATA_DATABASE_URL"


def database_url_from_env() -> str:
    """Return the configured ledger URL without logging or rewriting secrets."""

    database_url = os.environ.get(DATABASE_URL_ENV)
    if not database_url:
        raise RuntimeError(f"{DATABASE_URL_ENV} must configure the data-layer database")
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    if not database_url.startswith("postgresql+psycopg://"):
        raise RuntimeError(
            f"{DATABASE_URL_ENV} must be a PostgreSQL connection URL"
        )
    return database_url


def create_ledger_engine(database_url: str | None = None) -> Engine:
    """Create a pooled engine for the shared, flash-backed PostgreSQL ledger."""

    return create_engine(
        database_url or database_url_from_env(),
        pool_pre_ping=True,
    )


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create short-lived sessions whose objects remain readable after commit."""

    return sessionmaker(bind=engine, expire_on_commit=False)
