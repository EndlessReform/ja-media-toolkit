"""Read-only dependency preflight for the persistent data DEV deployment."""

from __future__ import annotations

from collections.abc import Callable
import json
import os
import re
from urllib.request import urlopen

from kombu import Connection
import psycopg

from ja_media_data.lakehouse.catalog import CatalogConfig, connect_catalog
from ja_media_data.settings import get_settings
from ja_media_data.storage.binding_overrides import control_schema_from_settings
from ja_media_data.storage.binding_schema import postgres_url_for_psycopg
from ja_media_data.storage.bronze import bronze_store_from_settings


def main() -> None:
    """Run every bounded DEV readiness check and exit nonzero on any failure."""

    settings = get_settings()
    config = CatalogConfig.from_settings(settings)
    checks: tuple[tuple[str, Callable[[], None]], ...] = (
        ("application_postgres", lambda: _application_postgres(config)),
        ("ducklake", lambda: _ducklake(config)),
        ("dagster_postgres", _dagster_postgres),
        ("rabbitmq", _rabbitmq),
        ("bronze_read", lambda: bronze_store_from_settings(settings).probe()),
        (
            "anilist_gateway",
            lambda: _http(
                settings.services.root_url + "/api/v1/anilist/healthz"
            ),
        ),
        ("http_gateway", _gateway),
    )
    results: dict[str, dict[str, str]] = {}
    failed = False
    for name, check in checks:
        try:
            check()
            results[name] = {"status": "ok"}
        except Exception as error:  # readiness must report every failed boundary
            failed = True
            results[name] = {
                "status": "failed",
                "error": _safe_error(error),
            }
    print(json.dumps(results, indent=2))
    if failed:
        raise SystemExit(1)


def _application_postgres(config: CatalogConfig) -> None:
    control_schema = control_schema_from_settings(
        catalog_schema=config.metadata_schema
    )
    with psycopg.connect(postgres_url_for_psycopg(config.postgres_url)) as connection:
        for schema, relation in (
            (config.metadata_schema, "ducklake_metadata"),
            (control_schema, "schema_history"),
        ):
            present = connection.execute(
                """SELECT EXISTS (
                       SELECT 1 FROM information_schema.tables
                       WHERE table_schema = %s AND table_name = %s
                   )""",
                (schema, relation),
            ).fetchone()[0]
            if not present:
                raise RuntimeError(f"missing {schema}.{relation}; run schema-init")


def _ducklake(config: CatalogConfig) -> None:
    with connect_catalog(config, initialize_catalog=False) as connection:
        connection.execute("SELECT count(*) FROM materializations").fetchone()


def _dagster_postgres() -> None:
    url = _required_env("DAGSTER_POSTGRES_URL")
    with psycopg.connect(postgres_url_for_psycopg(url)) as connection:
        if connection.execute("SELECT to_regclass('runs')").fetchone()[0] is None:
            raise RuntimeError("Dagster run storage is not initialized")


def _rabbitmq() -> None:
    with Connection(_required_env("JA_MEDIA_CELERY_BROKER_URL")) as connection:
        connection.ensure_connection(max_retries=1, timeout=5)


def _gateway() -> None:
    root = _required_env("JA_MEDIA_PREFLIGHT_GATEWAY_URL").rstrip("/")
    _http(root + "/dagster/server_info")
    _http(root + "/operator")


def _http(url: str) -> None:
    with urlopen(url, timeout=5) as response:  # noqa: S310 - configured DEV URLs
        if response.status >= 400:
            raise RuntimeError(f"HTTP {response.status} from {url}")


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is not set")
    return value


def _safe_error(error: Exception) -> str:
    """Keep dependency diagnostics useful without printing URL credentials."""

    message = f"{type(error).__name__}: {error}"
    return re.sub(
        r"([a-z][a-z0-9+.-]*://)[^\s/@:]+:[^\s/@]+@",
        r"\1***:***@",
        message,
        flags=re.IGNORECASE,
    )


if __name__ == "__main__":
    main()
