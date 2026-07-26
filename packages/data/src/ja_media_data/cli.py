"""Narrow operator application CLI; Dagster owns product execution."""

from __future__ import annotations

import argparse
import json
import os

from ja_media_data.lakehouse.catalog import CatalogConfig
from ja_media_data.lakehouse.repository import repository_from_settings
from ja_media_data.settings import CONFIG_PATH_ENV, config_path, get_settings


def main() -> None:
    parser = argparse.ArgumentParser(prog="ja-data")
    parser.add_argument(
        "--config",
        help="deployment TOML (default: packages/data/config.local.toml)",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    web = commands.add_parser("web", help="serve the operator workbench")
    web.add_argument("--port", type=int, default=8766)

    bind = commands.add_parser("bind", help="append a human binding decision")
    bind.add_argument("namespace")
    bind.add_argument("series_id")
    bind.add_argument("episode")
    target = bind.add_mutually_exclusive_group(required=True)
    target.add_argument("--capture", dest="audio_capture_id")
    target.add_argument("--unbind", action="store_true")
    bind.add_argument("--method", default="human")
    bind.add_argument("--note")

    commands.add_parser("doctor", help="validate effective configuration")
    worker = commands.add_parser("worker", help="run a native capability worker")
    worker_commands = worker.add_subparsers(dest="worker_command", required=True)
    worker_doctor = worker_commands.add_parser(
        "doctor", help="validate native worker access"
    )
    worker_doctor.add_argument("--profile", required=True)
    worker_doctor.add_argument(
        "--write-probe",
        action="store_true",
        help="write/read/delete one staging canary",
    )
    worker_start = worker_commands.add_parser(
        "start", help="start the foreground native worker"
    )
    worker_start.add_argument("--profile", required=True)
    args = parser.parse_args()
    if args.config:
        os.environ[CONFIG_PATH_ENV] = args.config

    if args.command == "web":
        from ja_media_data.operator.cli import run_web

        run_web(port=args.port)
    elif args.command == "bind":
        _bind(args)
    elif args.command == "worker":
        from ja_media_data.workers.cli import doctor, start

        if args.worker_command == "doctor":
            doctor(args.profile, write_probe=args.write_probe)
        else:
            start(args.profile)
    else:
        _doctor()


def _doctor() -> None:
    settings = get_settings()
    CatalogConfig.from_settings(settings)
    print(
        json.dumps(
            {
                "config": str(config_path()),
                "environment": settings.environment,
                "bronze": {
                    "endpoint_url": settings.bronze.endpoint_url,
                    "bucket": settings.bronze.bucket,
                    "prefix": settings.bronze.prefix,
                },
                "ducklake": {
                    "catalog_schema": settings.ducklake.catalog_schema,
                    "data_path": settings.ducklake.data_path,
                },
                "dagster_ui_url": settings.dagster.ui_url,
                "services_root_url": settings.services.root_url,
            },
            indent=2,
        )
    )


def _bind(args: argparse.Namespace) -> None:
    with repository_from_settings() as repository:
        override_id = repository.append_override(
            namespace=args.namespace,
            series_id=args.series_id,
            episode=args.episode,
            audio_capture_id=None if args.unbind else args.audio_capture_id,
            decision_method=args.method,
            decision_note=args.note,
        )
        current = repository.get_current_binding(
            args.namespace, args.series_id, args.episode
        )
    print(
        json.dumps(
            {
                "override_id": override_id,
                "locator": f"{args.namespace}:{args.series_id}:{args.episode}",
                "audio_capture_id": (
                    current.audio_capture_id if current is not None else None
                ),
                "status": "bound" if current is not None else "unbound",
            },
            indent=2,
        )
    )
