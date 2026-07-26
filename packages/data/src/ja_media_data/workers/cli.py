"""Native worker doctor and foreground consumer commands."""

from __future__ import annotations

import json
import os
from collections.abc import Callable

from kombu import Connection

from ja_media_data.storage.bronze import BronzeStore
from ja_media_data.workers.app import build_worker_app
from ja_media_data.workers.marker_store import MarkerStore
from ja_media_data.workers.settings import WorkerSettings, load_worker_settings


_FORBIDDEN_ENV = {
    "DAGSTER_POSTGRES_URL",
    "JA_MEDIA_DATA_DATABASE_URL",
    "JA_MEDIA_DUCKLAKE__POSTGRES_URL",
    "JA_MEDIA_CONTROL_DATABASE_URL",
}


def doctor(profile: str, *, write_probe: bool = False) -> None:
    """Validate profile, credentials, broker, and bounded object access."""

    _reject_database_environment()
    settings = load_worker_settings(profile)
    checks: dict[str, dict[str, str]] = {
        "configuration": {"status": "ok"}
    }
    _check(checks, "broker", lambda: _probe_broker(settings))
    _check(checks, "bronze_read", lambda: _bronze(settings).probe())
    _check(
        checks,
        "staging_bucket",
        lambda: _markers(settings).probe_bucket(),
    )
    if write_probe:
        _check(
            checks,
            "staging_write_read_delete",
            lambda: _markers(settings).probe(),
        )
    else:
        checks["staging_write_read_delete"] = {"status": "skipped"}
    print(
        json.dumps(
            {
                "profile": settings.profile.name,
                "queue": settings.profile.queue,
                "operations": settings.profile.operations,
                "loaded_files": [str(path) for path in settings.loaded_files],
                "checks": checks,
            },
            indent=2,
        )
    )
    if any(item["status"] == "failed" for item in checks.values()):
        raise SystemExit(1)


def start(profile: str) -> None:
    """Start the foreground native consumer for one checked profile."""

    _reject_database_environment()
    settings = load_worker_settings(profile)
    app = build_worker_app(settings)
    app.worker_main(
        [
            "worker",
            "--loglevel=INFO",
            "--queues",
            settings.profile.queue,
            f"--concurrency={settings.profile.concurrency}",
            "--pool=solo",
        ]
    )


def _bronze(settings: WorkerSettings) -> BronzeStore:
    return BronzeStore(
        endpoint_url=settings.bronze_endpoint_url,
        bucket=settings.bronze_bucket,
        prefix=settings.bronze_prefix,
        addressing_style=settings.bronze_addressing_style,
        access_key_id=settings.secrets.bronze_access_key_id,
        secret_access_key=settings.secrets.bronze_secret_access_key,
    )


def _markers(settings: WorkerSettings) -> MarkerStore:
    return MarkerStore(
        endpoint_url=settings.bronze_endpoint_url,
        bucket=settings.profile.staging_bucket,
        prefix=settings.profile.staging_prefix,
        region=settings.profile.staging_region,
        addressing_style=settings.bronze_addressing_style,
        access_key_id=settings.secrets.staging_access_key_id,
        secret_access_key=settings.secrets.staging_secret_access_key,
    )


def _reject_database_environment() -> None:
    found = sorted(name for name in _FORBIDDEN_ENV if os.environ.get(name))
    if found:
        raise RuntimeError(
            "native worker refuses database credentials: " + ", ".join(found)
        )


def _probe_broker(settings: WorkerSettings) -> None:
    with Connection(settings.broker_url, connect_timeout=5) as connection:
        connection.ensure_connection(max_retries=1)


def _check(
    checks: dict[str, dict[str, str]],
    name: str,
    operation: Callable[[], None],
) -> None:
    try:
        operation()
    except Exception as error:
        checks[name] = {
            "status": "failed",
            "error": f"{type(error).__name__}: {error}",
        }
    else:
        checks[name] = {"status": "ok"}
