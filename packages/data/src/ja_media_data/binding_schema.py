"""Checksum-protected PostgreSQL schema management for human decisions."""

from __future__ import annotations

import hashlib
from pathlib import Path

import psycopg
from psycopg import sql


_PACKAGED_SCHEMA_DIR = Path(__file__).parent / "postgres_schema"
POSTGRES_SCHEMA_DIR = (
    _PACKAGED_SCHEMA_DIR
    if _PACKAGED_SCHEMA_DIR.is_dir()
    else Path(__file__).parents[2] / "postgres_schema"
)
DEFAULT_CONTROL_SCHEMA = "ja_media_control"


def postgres_url_for_psycopg(value: str) -> str:
    """Normalize the configured PostgreSQL URL for psycopg."""

    return value.replace("postgresql+psycopg://", "postgresql://", 1)


def apply_postgres_schema(
    connection: psycopg.Connection[tuple], *,
    control_schema: str = DEFAULT_CONTROL_SCHEMA,
    schema_dir: Path = POSTGRES_SCHEMA_DIR,
) -> list[str]:
    """Apply checksum-protected control-plane SQL in its own PostgreSQL schema."""

    identifier = sql.Identifier(control_schema)
    with connection.transaction():
        connection.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(identifier))
        connection.execute(sql.SQL(
            """CREATE TABLE IF NOT EXISTS {}.schema_history (
                   filename TEXT PRIMARY KEY, checksum TEXT NOT NULL,
                   applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
               )"""
        ).format(identifier))
    applied = dict(connection.execute(
        sql.SQL("SELECT filename, checksum FROM {}.schema_history").format(identifier)
    ).fetchall())
    newly_applied: list[str] = []
    for path in sorted(schema_dir.glob("[0-9][0-9][0-9]_*.sql")):
        contents = path.read_text()
        checksum = hashlib.sha256(contents.encode()).hexdigest()
        if existing := applied.get(path.name):
            if existing != checksum:
                raise RuntimeError(f"applied PostgreSQL schema file changed: {path.name}")
            continue
        with connection.transaction():
            connection.execute(
                sql.SQL("SET LOCAL search_path TO {}, pg_catalog").format(identifier)
            )
            connection.execute(contents)
            connection.execute(sql.SQL(
                "INSERT INTO {}.schema_history (filename, checksum) VALUES (%s, %s)"
            ).format(identifier), (path.name, checksum))
        newly_applied.append(path.name)
    return newly_applied
