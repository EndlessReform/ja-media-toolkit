#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "duckdb==1.5.4",
#   "python-dotenv==1.2.2",
#   "pytz==2026.2",
# ]
# ///
"""Export one exact DEV DuckLake asset snapshot to local JSONL or Parquet."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import tomllib
from urllib.parse import quote, unquote, urlsplit

import duckdb
from dotenv import dotenv_values


REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_CONFIG = REPO_ROOT / "packages" / "data" / "config.dev.toml"
DEFAULT_OUTPUT = REPO_ROOT / "output" / "ducklake"
IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")
ASSET_TARGETS = {
    "bronze_captures": "bronze_captures",
    "episode_hints_auto": "episode_resolution",
    "episode_binding_proposals": "episode_resolution",
    "resolution_issues_auto": "episode_resolution",
    "capture_audio_eligibility": "capture_audio_eligibility",
    "accepted_bindings_auto": "accepted_bindings",
    "canonical_episode_inputs": "canonical_inputs",
    "canonical_subtitle_inputs": "canonical_inputs",
    "subtitle_language_results": "subtitle_lid",
}


def main() -> None:
    args = _parser().parse_args()
    asset = args.asset
    if not IDENTIFIER.fullmatch(asset):
        raise SystemExit(f"unsafe asset: {asset!r}")
    config_path = Path(
        os.environ.get("JA_MEDIA_DATA_CONFIG", str(DEFAULT_CONFIG))
    ).expanduser().resolve()
    if not config_path.is_file():
        raise SystemExit(
            f"DEV data config not found: {config_path}; copy "
            "packages/data/config.dev.example.toml to packages/data/config.dev.toml"
        )
    config = _load_config(config_path)
    connection = _connect(config)
    try:
        selection = _resolve_selection(connection, asset, args)
        suffix = "jsonl" if args.format == "jsonl" else "parquet"
        output = (
            args.output
            or DEFAULT_OUTPUT / f"{asset}-snapshot-{selection['snapshot_id']}.{suffix}"
        ).expanduser().resolve()
        rows = _export(
            connection,
            asset=asset,
            snapshot_id=selection["snapshot_id"],
            output=output,
            output_format=args.format,
            force=args.force,
        )
    finally:
        connection.close()
    with output.open("rb") as stream:
        checksum = hashlib.file_digest(stream, "sha256").hexdigest()
    result = {
        "asset": asset,
        "target": selection.get("target"),
        "scope": selection.get("scope"),
        "selection": selection["selection"],
        "materialization_id": selection.get("materialization_id"),
        "snapshot_id": selection["snapshot_id"],
        "computed_at": selection.get("computed_at"),
        "rows": rows,
        "format": args.format,
        "output": str(output),
        "sha256": checksum,
    }
    print(json.dumps(result, indent=2, sort_keys=True))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Export a DuckLake-backed DEV asset through an exact read-only snapshot. "
            "With no selector, export the latest durable materialization."
        )
    )
    parser.add_argument("asset", help="DuckLake table / Dagster asset name")
    selector = parser.add_mutually_exclusive_group()
    selector.add_argument(
        "--materialization-id", "--materialization", help="exact materialization ID"
    )
    selector.add_argument("--snapshot", type=int, help="exact DuckLake snapshot ID")
    parser.add_argument("--format", choices=("jsonl", "parquet"), default="jsonl")
    parser.add_argument("--output", type=Path, help="local output path")
    parser.add_argument("--force", action="store_true", help="replace local output")
    return parser


def _load_config(path: Path) -> dict[str, str]:
    document = tomllib.loads(path.read_text())
    values = document.get("ducklake")
    if not isinstance(values, dict):
        raise SystemExit(f"missing [ducklake] section in {path}")
    environment = path.name.removeprefix("config.").removesuffix(".toml")
    secrets = dotenv_values(path.with_name(f".env.{environment}"))

    def setting(env: str, key: str, *, default: str | None = None) -> str:
        value = os.environ.get(env) or secrets.get(env) or values.get(key) or default
        if not isinstance(value, str) or not value:
            raise SystemExit(
                f"missing {env}; set it in {path.with_name(f'.env.{environment}')}"
            )
        return value

    return {
        "postgres_url": setting("JA_MEDIA_DUCKLAKE__POSTGRES_URL", "postgres_url"),
        "metadata_schema": setting(
            "JA_MEDIA_DUCKLAKE__CATALOG_SCHEMA", "catalog_schema"
        ),
        "data_path": setting("JA_MEDIA_DUCKLAKE__DATA_PATH", "data_path"),
        "s3_endpoint_url": setting(
            "JA_MEDIA_DUCKLAKE__S3_ENDPOINT_URL", "s3_endpoint_url"
        ),
        "s3_region": setting(
            "JA_MEDIA_DUCKLAKE__S3_REGION", "s3_region", default="garage"
        ),
        "s3_key_id": setting(
            "JA_MEDIA_DUCKLAKE__S3_ACCESS_KEY_ID", "s3_access_key_id"
        ),
        "s3_secret": setting(
            "JA_MEDIA_DUCKLAKE__S3_SECRET_ACCESS_KEY", "s3_secret_access_key"
        ),
    }


def _connect(config: dict[str, str]) -> duckdb.DuckDBPyConnection:
    target = _postgres_target(config["postgres_url"])
    endpoint = urlsplit(config["s3_endpoint_url"])
    if endpoint.scheme not in {"http", "https"} or not endpoint.netloc:
        raise SystemExit("DuckLake S3 endpoint must be an http(s) URL")
    connection = duckdb.connect()
    try:
        connection.execute("INSTALL ducklake; LOAD ducklake")
        connection.execute("INSTALL postgres; LOAD postgres")
        connection.execute("INSTALL httpfs; LOAD httpfs")
        connection.execute(
            "CREATE SECRET lakehouse_postgres (TYPE postgres, HOST "
            + _sql_string(target[0]) + ", PORT " + str(target[1])
            + ", DATABASE " + _sql_string(target[2])
            + ", USER " + _sql_string(target[3])
            + ", PASSWORD " + _sql_string(target[4]) + ")"
        )
        connection.execute(
            "CREATE SECRET lakehouse_s3 (TYPE s3, PROVIDER config, KEY_ID "
            + _sql_string(config["s3_key_id"])
            + ", SECRET " + _sql_string(config["s3_secret"])
            + ", REGION " + _sql_string(config["s3_region"])
            + ", ENDPOINT " + _sql_string(endpoint.netloc)
            + ", URL_STYLE 'path', USE_SSL "
            + ("true" if endpoint.scheme == "https" else "false")
            + ", SCOPE " + _sql_string(config["data_path"]) + ")"
        )
        connection.execute(
            "ATTACH 'ducklake:postgres:' AS lakehouse (META_SECRET 'lakehouse_postgres', "
            "METADATA_SCHEMA " + _sql_string(config["metadata_schema"])
            + ", READ_ONLY)"
        )
        connection.execute("USE lakehouse")
        return connection
    except Exception:
        connection.close()
        raise


def _postgres_target(url: str) -> tuple[str, int, str, str, str]:
    normalized = url.replace("postgresql+psycopg://", "postgresql://", 1)
    try:
        scheme, raw = normalized.split("://", 1)
        auth, target = raw.rsplit("@", 1)
        user, password = auth.split(":", 1)
    except ValueError as error:
        raise SystemExit("DEV DuckLake PostgreSQL URL is incomplete or invalid") from error
    encoded = (
        f"{scheme}://{quote(unquote(user), safe='')}:"
        f"{quote(unquote(password), safe='')}@{target}"
    )
    parsed = urlsplit(encoded)
    if parsed.scheme not in {"postgres", "postgresql"} or not all(
        (parsed.hostname, parsed.username, parsed.password, parsed.path.lstrip("/"))
    ):
        raise SystemExit("DEV DuckLake PostgreSQL URL is incomplete or invalid")
    return (
        parsed.hostname, parsed.port or 5432, parsed.path.lstrip("/"),
        unquote(parsed.username), unquote(parsed.password),
    )


def _resolve_selection(
    connection: duckdb.DuckDBPyConnection, asset: str, args: argparse.Namespace
) -> dict[str, object]:
    expected = ASSET_TARGETS.get(asset)
    if expected is None:
        raise SystemExit(f"asset is not mapped to a materialization target: {asset}")
    if args.materialization_id:
        row = connection.execute(
            """SELECT target, scope, snapshot_id, computed_at
                 FROM materializations WHERE materialization_id = ?""",
            [args.materialization_id],
        ).fetchone()
        if row is None:
            raise SystemExit(f"materialization not found: {args.materialization_id}")
        target, scope, snapshot_id, computed_at = row
        if str(target) != expected:
            raise SystemExit(
                f"asset {asset} belongs to {expected}, not materialization target {target}"
            )
        if snapshot_id is None:
            raise SystemExit(f"materialization has no DuckLake snapshot: {args.materialization_id}")
        return {
            "selection": "materialization",
            "target": str(target),
            "scope": str(scope),
            "materialization_id": args.materialization_id,
            "snapshot_id": int(snapshot_id),
            "computed_at": str(computed_at),
        }
    if args.snapshot is not None:
        if args.snapshot < 0:
            raise SystemExit("snapshot must be non-negative")
        return {
            "selection": "snapshot",
            "target": expected,
            "snapshot_id": args.snapshot,
        }
    row = connection.execute(
        """SELECT materialization_id, snapshot_id, computed_at
             FROM materializations
            WHERE target = ? AND scope = 'corpus' AND snapshot_id IS NOT NULL
            ORDER BY computed_at DESC, materialization_id DESC LIMIT 1""",
        [expected],
    ).fetchone()
    if row is None:
        raise SystemExit(f"no committed materialization found for target {expected}")
    return {
        "selection": "latest",
        "target": expected,
        "scope": "corpus",
        "materialization_id": str(row[0]),
        "snapshot_id": int(row[1]),
        "computed_at": str(row[2]),
    }


def _export(
    connection: duckdb.DuckDBPyConnection, *, asset: str, snapshot_id: object,
    output: Path, output_format: str, force: bool,
) -> int:
    if output.exists() and not force:
        raise SystemExit(f"output already exists; pass --force to replace it: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    source = f"{asset} AT (VERSION => {int(snapshot_id)})"
    rows = int(connection.execute(f"SELECT count(*) FROM {source}").fetchone()[0])
    handle = tempfile.NamedTemporaryFile(
        dir=output.parent, prefix=f".{output.name}.", suffix=".tmp", delete=False
    )
    temporary = Path(handle.name)
    handle.close()
    temporary.unlink()
    try:
        options = "FORMAT JSON" if output_format == "jsonl" else "FORMAT PARQUET, COMPRESSION ZSTD"
        connection.execute(
            f"COPY (SELECT * FROM {source}) TO {_sql_string(str(temporary))} ({options})"
        )
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return rows


def _sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


if __name__ == "__main__":
    main()
