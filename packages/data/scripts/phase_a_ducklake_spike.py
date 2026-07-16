#!/usr/bin/env python3
"""Run the disposable DuckLake-on-Garage Phase A substrate spike.

This is intentionally operational glue rather than production lakehouse code.
It confines destructive catalog recovery work to a schema whose name ends in
``_phase_a`` and confines object writes to a prefix containing ``phase-a``.
Credentials are loaded from the environment into temporary DuckDB secrets and
are never included in the report.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

import boto3
import duckdb
import psycopg
from botocore.config import Config


CATALOG_ALIAS = "phase_a"
DEFAULT_CATALOG_SCHEMA = "ja_media_ducklake_phase_a"
DEFAULT_DATA_PREFIX = "audio/anime/lakehouse/phase-a"


@dataclass(frozen=True)
class PostgresTarget:
    """A parsed PostgreSQL target with secret-bearing fields kept in memory."""

    host: str
    port: int
    database: str
    user: str
    password: str


@dataclass(frozen=True)
class SpikeConfig:
    """Validated boundaries and connection settings for the spike."""

    postgres: PostgresTarget
    catalog_schema: str
    bucket: str
    data_prefix: str
    endpoint_url: str
    region: str
    addressing_style: str

    @property
    def data_path(self) -> str:
        return f"s3://{self.bucket}/{self.data_prefix}/"


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} must be set")
    return value


def _parse_postgres_url(value: str) -> PostgresTarget:
    normalized = value.replace("postgresql+psycopg://", "postgresql://", 1)
    parsed = urlsplit(normalized)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise RuntimeError("JA_MEDIA_DATA_DATABASE_URL must be PostgreSQL")
    if not all((parsed.hostname, parsed.username, parsed.password, parsed.path)):
        raise RuntimeError("JA_MEDIA_DATA_DATABASE_URL must include host, user, password, and database")
    return PostgresTarget(
        host=parsed.hostname,
        port=parsed.port or 5432,
        database=parsed.path.lstrip("/"),
        user=unquote(parsed.username),
        password=unquote(parsed.password),
    )


def load_config() -> SpikeConfig:
    """Load secrets and enforce the disposable Phase A naming guardrails."""
    schema = os.environ.get("JA_MEDIA_DUCKLAKE_CATALOG_SCHEMA", DEFAULT_CATALOG_SCHEMA)
    prefix = os.environ.get("JA_MEDIA_DUCKLAKE_DATA_PREFIX", DEFAULT_DATA_PREFIX).strip("/")
    if not schema.endswith("_phase_a") or not schema.replace("_", "").isalnum():
        raise RuntimeError("catalog schema must be alphanumeric/underscore and end in _phase_a")
    if "phase-a" not in prefix.split("/"):
        raise RuntimeError("data prefix must contain a phase-a path component")
    addressing_style = os.environ.get("JA_MEDIA_S3_ADDRESSING_STYLE", "path")
    if addressing_style != "path":
        raise RuntimeError("Phase A requires JA_MEDIA_S3_ADDRESSING_STYLE=path")
    return SpikeConfig(
        postgres=_parse_postgres_url(_required_env("JA_MEDIA_DATA_DATABASE_URL")),
        catalog_schema=schema,
        bucket=_required_env("JA_MEDIA_BRONZE_BUCKET"),
        data_prefix=prefix,
        endpoint_url=_required_env("JA_MEDIA_S3_ENDPOINT_URL"),
        region=os.environ.get("AWS_DEFAULT_REGION", "garage"),
        addressing_style=addressing_style,
    )


def _sql_string(value: str) -> str:
    """Quote a value for a DuckDB statement assembled only in process memory."""
    return "'" + value.replace("'", "''") + "'"


def _postgres_connection(config: SpikeConfig) -> psycopg.Connection:
    target = config.postgres
    return psycopg.connect(
        host=target.host,
        port=target.port,
        dbname=target.database,
        user=target.user,
        password=target.password,
        autocommit=True,
    )


def _prepare_catalog_schema(config: SpikeConfig, *, reset: bool) -> None:
    with _postgres_connection(config) as connection:
        exists = connection.execute(
            "SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = %s)",
            (config.catalog_schema,),
        ).fetchone()[0]
        identifier = psycopg.sql.Identifier(config.catalog_schema)
        if exists and not reset:
            raise RuntimeError(
                f"catalog schema {config.catalog_schema!r} already exists; pass --reset to replace it"
            )
        if exists:
            connection.execute(psycopg.sql.SQL("DROP SCHEMA {} CASCADE").format(identifier))
        connection.execute(psycopg.sql.SQL("CREATE SCHEMA {}").format(identifier))


def _duckdb_connection(config: SpikeConfig, *, create: bool = False) -> duckdb.DuckDBPyConnection:
    """Attach the shared catalog using temporary Postgres and scoped S3 secrets."""
    endpoint = urlsplit(config.endpoint_url)
    if endpoint.scheme not in {"http", "https"} or not endpoint.netloc:
        raise RuntimeError("JA_MEDIA_S3_ENDPOINT_URL must be an http(s) URL")
    connection = duckdb.connect()
    connection.execute("INSTALL ducklake; LOAD ducklake")
    connection.execute("INSTALL postgres; LOAD postgres")
    connection.execute("INSTALL httpfs; LOAD httpfs")
    pg = config.postgres
    connection.execute(
        "CREATE SECRET phase_a_postgres ("
        "TYPE postgres, HOST " + _sql_string(pg.host) + ", PORT " + str(pg.port)
        + ", DATABASE " + _sql_string(pg.database) + ", USER " + _sql_string(pg.user)
        + ", PASSWORD " + _sql_string(pg.password) + ")"
    )
    connection.execute(
        "CREATE SECRET phase_a_s3 (TYPE s3, PROVIDER config, KEY_ID "
        + _sql_string(_required_env("AWS_ACCESS_KEY_ID"))
        + ", SECRET " + _sql_string(_required_env("AWS_SECRET_ACCESS_KEY"))
        + ", REGION " + _sql_string(config.region)
        + ", ENDPOINT " + _sql_string(endpoint.netloc)
        + ", URL_STYLE 'path', USE_SSL " + ("true" if endpoint.scheme == "https" else "false")
        + ", SCOPE " + _sql_string(config.data_path) + ")"
    )
    parameters = [
        "META_SECRET 'phase_a_postgres'",
        "METADATA_SCHEMA " + _sql_string(config.catalog_schema),
        "DATA_INLINING_ROW_LIMIT 10",
    ]
    if create:
        parameters.insert(0, "DATA_PATH " + _sql_string(config.data_path))
    connection.execute(
        "ATTACH 'ducklake:postgres:' AS " + CATALOG_ALIAS + " (" + ", ".join(parameters) + ")"
    )
    return connection


def _s3_client(config: SpikeConfig):
    return boto3.client(
        "s3",
        endpoint_url=config.endpoint_url,
        region_name=config.region,
        aws_access_key_id=_required_env("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=_required_env("AWS_SECRET_ACCESS_KEY"),
        config=Config(s3={"addressing_style": config.addressing_style}),
    )


def _object_keys(config: SpikeConfig, suffix: str = "") -> list[str]:
    prefix = f"{config.data_prefix}/{suffix}".rstrip("/") + "/"
    paginator = _s3_client(config).get_paginator("list_objects_v2")
    return [item["Key"] for page in paginator.paginate(Bucket=config.bucket, Prefix=prefix) for item in page.get("Contents", [])]


def _reset_data_prefix(config: SpikeConfig) -> int:
    """Delete only objects beneath the validated disposable Phase A prefix."""
    keys = _object_keys(config)
    client = _s3_client(config)
    for offset in range(0, len(keys), 1000):
        client.delete_objects(
            Bucket=config.bucket,
            Delete={"Objects": [{"Key": key} for key in keys[offset : offset + 1000]]},
        )
    return len(keys)


def _append_worker(worker: str, start: multiprocessing.synchronize.Event, ready, results) -> None:
    """Append from an independent DuckDB client after both clients attach."""
    try:
        connection = _duckdb_connection(load_config())
        ready.put(worker)
        if not start.wait(timeout=30):
            raise TimeoutError("concurrency barrier timed out")
        connection.execute(
            f"INSERT INTO {CATALOG_ALIAS}.events (source, sequence, note) "
            "SELECT ?, range, 'concurrent' FROM range(20)",
            [worker],
        )
        connection.close()
        results.put((worker, None))
    except Exception as error:  # noqa: BLE001 - subprocess must report failures to its parent
        results.put((worker, repr(error)))


def exercise_concurrent_appends() -> dict[str, int]:
    """Run two simultaneous processes, each with an independent attachment."""
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    ready = context.Queue()
    results = context.Queue()
    workers = [context.Process(target=_append_worker, args=(name, start, ready, results)) for name in ("client-a", "client-b")]
    for worker in workers:
        worker.start()
    attached = {ready.get(timeout=45), ready.get(timeout=45)}
    if attached != {"client-a", "client-b"}:
        raise RuntimeError(f"unexpected attached workers: {attached}")
    start.set()
    failures = dict(results.get(timeout=45) for _ in workers)
    for worker in workers:
        worker.join(timeout=45)
    failures = {name: error for name, error in failures.items() if error}
    if failures:
        raise RuntimeError(f"concurrent append failure: {failures}")
    with _duckdb_connection(load_config()) as connection:
        rows = connection.execute(
            f"SELECT source, count(*) FROM {CATALOG_ALIAS}.events "
            "WHERE note = 'concurrent' GROUP BY source ORDER BY source"
        ).fetchall()
    return dict(rows)


def _pg_environment(config: SpikeConfig) -> dict[str, str]:
    target = config.postgres
    return os.environ | {
        "PGHOST": target.host,
        "PGPORT": str(target.port),
        "PGDATABASE": target.database,
        "PGUSER": target.user,
        "PGPASSWORD": target.password,
    }


def _postgres_tool(name: str) -> str:
    """Prefer Homebrew's current libpq tools over an older versioned keg."""
    candidates = []
    if configured_dir := os.environ.get("JA_MEDIA_PG_BIN_DIR"):
        candidates.append(Path(configured_dir) / name)
    candidates.append(Path("/opt/homebrew/opt/libpq/bin") / name)
    if discovered := shutil.which(name):
        candidates.append(Path(discovered))
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    raise RuntimeError(f"no executable {name} client was found")


def recovery_drill(config: SpikeConfig) -> int:
    """Dump, destroy, and restore only the guarded catalog schema."""
    with tempfile.TemporaryDirectory(prefix="ja-media-ducklake-phase-a-") as temp_dir:
        dump_path = Path(temp_dir) / "catalog.dump"
        subprocess.run(
            [_postgres_tool("pg_dump"), "--format=custom", f"--schema={config.catalog_schema}", f"--file={dump_path}"],
            env=_pg_environment(config),
            check=True,
        )
        with _postgres_connection(config) as connection:
            identifier = psycopg.sql.Identifier(config.catalog_schema)
            connection.execute(psycopg.sql.SQL("DROP SCHEMA {} CASCADE").format(identifier))
        subprocess.run(
            [_postgres_tool("pg_restore"), "--no-owner", "--no-privileges", f"--dbname={config.postgres.database}", str(dump_path)],
            env=_pg_environment(config),
            check=True,
        )
    with _duckdb_connection(config) as connection:
        return connection.execute(f"SELECT count(*) FROM {CATALOG_ALIAS}.events").fetchone()[0]


def run_spike(*, reset: bool) -> dict[str, object]:
    """Execute all single-machine Phase A checks and return a secret-free report."""
    config = load_config()
    reset_objects = _reset_data_prefix(config) if reset else 0
    _prepare_catalog_schema(config, reset=reset)
    with _duckdb_connection(config, create=True) as connection:
        connection.execute(
            f"CREATE TABLE {CATALOG_ALIAS}.events (source VARCHAR, sequence BIGINT)"
        )
        connection.execute(
            f"INSERT INTO {CATALOG_ALIAS}.events VALUES ('inline', 1), ('inline', 2), ('inline', 3)"
        )
        inline_objects = len(_object_keys(config, "main/events"))
        flushed = connection.execute(
            f"FROM ducklake_flush_inlined_data('{CATALOG_ALIAS}', table_name => 'events')"
        ).fetchall()
        flushed_objects = len(_object_keys(config, "main/events"))
        connection.execute(
            f"INSERT INTO {CATALOG_ALIAS}.events SELECT 'bulk', range FROM range(25)"
        )
        pre_alter_snapshot = connection.execute(
            f"FROM {CATALOG_ALIAS}.current_snapshot()"
        ).fetchone()[0]
        connection.execute(f"ALTER TABLE {CATALOG_ALIAS}.events ADD COLUMN note VARCHAR")
        connection.execute(
            f"INSERT INTO {CATALOG_ALIAS}.events VALUES ('after-alter', 1, 'new-column')"
        )
        historical_count = connection.execute(
            f"SELECT count(*) FROM {CATALOG_ALIAS}.events AT (VERSION => {pre_alter_snapshot})"
        ).fetchone()[0]
        current_count = connection.execute(f"SELECT count(*) FROM {CATALOG_ALIAS}.events").fetchone()[0]
        connection.execute(f"CREATE TABLE {CATALOG_ALIAS}.cleanup_probe AS SELECT range AS value FROM range(1000)")
        cleanup_before = len(_object_keys(config, "main/cleanup_probe"))
        connection.execute(f"DROP TABLE {CATALOG_ALIAS}.cleanup_probe")
        snapshots_before_expiry = connection.execute(
            f"SELECT count(*) FROM {CATALOG_ALIAS}.snapshots()"
        ).fetchone()[0]
        connection.execute(
            f"CALL ducklake_expire_snapshots('{CATALOG_ALIAS}', older_than => now() + INTERVAL '1 second')"
        )
        connection.execute(f"CALL ducklake_cleanup_old_files('{CATALOG_ALIAS}', cleanup_all => true)")
        cleanup_after = len(_object_keys(config, "main/cleanup_probe"))
    concurrent_rows = exercise_concurrent_appends()
    restored_rows = recovery_drill(config)
    checks = {
        "small_append_was_inlined": inline_objects == 0,
        "inline_flush_created_parquet": bool(flushed) and flushed_objects > 0,
        "alter_and_time_travel": historical_count == 28 and current_count == 29,
        "snapshot_expiry_and_cleanup": cleanup_before > 0 and cleanup_after == 0,
        "two_process_concurrent_appends": concurrent_rows == {"client-a": 20, "client-b": 20},
        "catalog_dump_drop_restore": restored_rows == 69,
    }
    report = {
        "catalog_database": config.postgres.database,
        "catalog_schema": config.catalog_schema,
        "data_path": config.data_path,
        "duckdb_version": duckdb.__version__,
        "reset_objects": reset_objects,
        "snapshots_before_expiry": snapshots_before_expiry,
        "concurrent_rows": concurrent_rows,
        "restored_rows": restored_rows,
        "checks": checks,
        "literal_two_machine_check": "pending; this run used two independent processes on one machine",
    }
    if not all(checks.values()):
        raise RuntimeError("Phase A checks failed:\n" + json.dumps(report, indent=2, sort_keys=True))
    return report


def verify_catalog() -> dict[str, object]:
    """Read the restored spike catalog, suitable for a second operator machine."""
    config = load_config()
    with _duckdb_connection(config) as connection:
        row_count = connection.execute(f"SELECT count(*) FROM {CATALOG_ALIAS}.events").fetchone()[0]
        settings = connection.execute(f"FROM {CATALOG_ALIAS}.settings()").fetchone()
    return {"row_count": row_count, "catalog_type": settings[0], "data_path": settings[2]}


def append_probe(source: str, rows: int) -> dict[str, object]:
    """Append a labeled batch for the literal two-machine concurrency check."""
    if not source.strip():
        raise RuntimeError("--source must not be blank")
    if not 1 <= rows <= 10_000:
        raise RuntimeError("--rows must be between 1 and 10,000")
    with _duckdb_connection(load_config()) as connection:
        connection.execute(
            f"INSERT INTO {CATALOG_ALIAS}.events (source, sequence, note) "
            "SELECT ?, range, 'cross-machine' FROM range(?)",
            [source, rows],
        )
        committed = connection.execute(
            f"SELECT count(*) FROM {CATALOG_ALIAS}.events "
            "WHERE source = ? AND note = 'cross-machine'",
            [source],
        ).fetchone()[0]
    return {"source": source, "rows_for_source": committed}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run", help="run the destructive, guarded Phase A spike")
    run_parser.add_argument("--reset", action="store_true", help="replace the existing *_phase_a catalog schema")
    subparsers.add_parser("recover", help="dump, drop, restore, and verify the existing guarded catalog")
    subparsers.add_parser("verify", help="read the restored catalog without changing it")
    append_parser = subparsers.add_parser("append", help="append a labeled cross-machine probe batch")
    append_parser.add_argument("--source", required=True, help="unique machine/run label")
    append_parser.add_argument("--rows", type=int, default=20)
    args = parser.parse_args()
    if args.command == "run":
        result = run_spike(reset=args.reset)
    elif args.command == "recover":
        result = {"restored_rows": recovery_drill(load_config())}
    elif args.command == "append":
        result = append_probe(args.source, args.rows)
    else:
        result = verify_catalog()
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
