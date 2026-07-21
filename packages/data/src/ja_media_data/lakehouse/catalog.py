"""Attach DuckLake to PostgreSQL metadata and apply reviewed schema files.

PostgreSQL is deliberately used only as DuckLake's transactional catalog. The
domain tables created here live at ``data_path`` as Parquet (or temporarily as
DuckLake inlined rows), and callers interact with them through DuckDB SQL.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit

import duckdb
import psycopg
from psycopg import sql

from ja_media_data.settings import DataSettings, get_settings


_PACKAGED_SCHEMA_DIR = Path(__file__).parents[1] / "migrations" / "ducklake"
SCHEMA_DIR = (
    _PACKAGED_SCHEMA_DIR
    if _PACKAGED_SCHEMA_DIR.is_dir()
    else Path(__file__).parents[3] / "migrations" / "ducklake"
)
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
@dataclass(frozen=True)
class CatalogConfig:
    """Connection settings for one DuckLake catalog and its data files."""

    postgres_url: str
    metadata_schema: str
    data_path: str
    alias: str = "lakehouse"
    s3_endpoint_url: str | None = None
    s3_region: str = "us-east-1"
    s3_key_id: str | None = None
    s3_secret: str | None = None

    def __post_init__(self) -> None:
        for label, value in (
            ("metadata schema", self.metadata_schema),
            ("catalog alias", self.alias),
        ):
            if not _IDENTIFIER.fullmatch(value):
                raise ValueError(f"{label} must be a SQL identifier")
        if self.data_path.startswith("s3://") and not all(
            (self.s3_endpoint_url, self.s3_key_id, self.s3_secret)
        ):
            raise ValueError("S3 data paths require endpoint, key ID, and secret")

    @classmethod
    def from_settings(cls, settings: DataSettings | None = None) -> CatalogConfig:
        """Build catalog configuration from validated deployment settings."""

        configured = (settings or get_settings()).ducklake
        return cls(
            postgres_url=configured.postgres_url,
            metadata_schema=configured.catalog_schema,
            data_path=configured.data_path,
            s3_endpoint_url=configured.s3_endpoint_url,
            s3_region=configured.s3_region,
            s3_key_id=configured.s3_access_key_id,
            s3_secret=configured.s3_secret_access_key,
        )


@dataclass(frozen=True)
class _PostgresTarget:
    host: str
    port: int
    database: str
    user: str
    password: str


def _parse_postgres_url(value: str) -> _PostgresTarget:
    normalized = value.replace("postgresql+psycopg://", "postgresql://", 1)
    parsed = urlsplit(normalized)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise ValueError("catalog URL must be PostgreSQL")
    if not all((parsed.hostname, parsed.username, parsed.password, parsed.path)):
        raise ValueError("catalog URL must include host, user, password, and database")
    return _PostgresTarget(
        host=parsed.hostname,
        port=parsed.port or 5432,
        database=parsed.path.lstrip("/"),
        user=unquote(parsed.username),
        password=unquote(parsed.password),
    )


def _sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _prepare_metadata_schema(config: CatalogConfig) -> bool:
    """Create the metadata namespace and report whether it is uninitialized."""

    target = _parse_postgres_url(config.postgres_url)
    with psycopg.connect(
        host=target.host,
        port=target.port,
        dbname=target.database,
        user=target.user,
        password=target.password,
        autocommit=True,
    ) as connection:
        connection.execute(
            sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(
                sql.Identifier(config.metadata_schema)
            )
        )
        return not connection.execute(
            """SELECT EXISTS (
                   SELECT 1
                   FROM information_schema.tables
                   WHERE table_schema = %s AND table_name = 'ducklake_metadata'
               )""",
            (config.metadata_schema,),
        ).fetchone()[0]


def connect_catalog(
    config: CatalogConfig, *, initialize_catalog: bool = True
) -> duckdb.DuckDBPyConnection:
    """Return a DuckDB client attached to an existing or initialized DuckLake.

    Read-only applications pass ``initialize_catalog=False`` so opening a
    screen cannot create PostgreSQL schemas or initialize a new catalog.
    """

    initialize = _prepare_metadata_schema(config) if initialize_catalog else False
    target = _parse_postgres_url(config.postgres_url)
    connection = duckdb.connect()
    try:
        connection.execute("INSTALL ducklake; LOAD ducklake")
        connection.execute("INSTALL postgres; LOAD postgres")
        connection.execute(
            "CREATE SECRET lakehouse_postgres (TYPE postgres, HOST "
            + _sql_string(target.host)
            + ", PORT "
            + str(target.port)
            + ", DATABASE "
            + _sql_string(target.database)
            + ", USER "
            + _sql_string(target.user)
            + ", PASSWORD "
            + _sql_string(target.password)
            + ")"
        )
        if config.data_path.startswith("s3://"):
            endpoint = urlsplit(config.s3_endpoint_url or "")
            if endpoint.scheme not in {"http", "https"} or not endpoint.netloc:
                raise ValueError("S3 endpoint must be an http(s) URL")
            connection.execute("INSTALL httpfs; LOAD httpfs")
            connection.execute(
                "CREATE SECRET lakehouse_s3 (TYPE s3, PROVIDER config, KEY_ID "
                + _sql_string(config.s3_key_id or "")
                + ", SECRET "
                + _sql_string(config.s3_secret or "")
                + ", REGION "
                + _sql_string(config.s3_region)
                + ", ENDPOINT "
                + _sql_string(endpoint.netloc)
                + ", URL_STYLE 'path', USE_SSL "
                + ("true" if endpoint.scheme == "https" else "false")
                + ", SCOPE "
                + _sql_string(config.data_path)
                + ")"
            )
        parameters = [
            "META_SECRET 'lakehouse_postgres'",
            "METADATA_SCHEMA " + _sql_string(config.metadata_schema),
        ]
        if initialize:
            parameters.insert(0, "DATA_PATH " + _sql_string(config.data_path))
        connection.execute(
            f"ATTACH 'ducklake:postgres:' AS {config.alias} ({', '.join(parameters)})"
        )
        connection.execute(f"USE {config.alias}")
        return connection
    except Exception:
        connection.close()
        raise


def apply_schema(
    connection: duckdb.DuckDBPyConnection,
    schema_dir: Path = SCHEMA_DIR,
) -> list[str]:
    """Apply new numbered SQL files transactionally and verify old checksums."""

    connection.execute(
        """CREATE TABLE IF NOT EXISTS schema_history (
               filename VARCHAR,
               checksum VARCHAR,
               applied_at TIMESTAMPTZ
           )"""
    )
    applied = dict(
        connection.execute("SELECT filename, checksum FROM schema_history").fetchall()
    )
    newly_applied: list[str] = []
    for path in sorted(schema_dir.glob("[0-9][0-9][0-9]_*.sql")):
        contents = path.read_text()
        checksum = hashlib.sha256(contents.encode()).hexdigest()
        if existing := applied.get(path.name):
            if existing != checksum:
                raise RuntimeError(f"applied schema file changed: {path.name}")
            continue
        connection.execute("BEGIN TRANSACTION")
        try:
            connection.execute(contents)
            connection.execute(
                "INSERT INTO schema_history VALUES (?, ?, ?)",
                [path.name, checksum, datetime.now(UTC)],
            )
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
        newly_applied.append(path.name)
    return newly_applied
