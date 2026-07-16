"""Local developer commands for dry-running and inspecting episode resolution."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
import json
from typing import Any

from ja_media_data.bronze_store import bronze_store_from_env
from ja_media_data.episode_metadata import AniListEpisodeMetadataProvider
from ja_media_data.lakehouse.bronze_hydration import hydrate_bronze_captures
from ja_media_data.lakehouse.repository import repository_from_env
from ja_media_data.resolution_service import ResolutionResult, resolve_batch


def main() -> None:
    """Run the small, deliberately developer-facing resolver CLI."""

    parser = argparse.ArgumentParser(prog="ja-data")
    commands = parser.add_subparsers(dest="command", required=True)
    sample = commands.add_parser(
        "resolve-sample", help="dry-run or apply a bounded Garage capture slice"
    )
    sample.add_argument("--limit", type=int, default=100)
    sample.add_argument(
        "--apply",
        action="store_true",
        help="commit capture headers, hints, bindings, and issues to the configured DB",
    )
    sample.add_argument(
        "--show", choices=("issues", "all", "none"), default="issues"
    )
    sample.add_argument("--jsonl", action="store_true")

    report = commands.add_parser(
        "resolution-report", help="inspect current resolution products and open issues"
    )
    report.add_argument("--limit", type=int, default=100)
    report.add_argument("--jsonl", action="store_true")

    scan = commands.add_parser(
        "scan-bronze", help="hydrate the rebuildable cache from Garage manifests"
    )
    scan.add_argument("--limit", type=int, default=100)

    commands.add_parser(
        "apply-lakehouse-schema",
        help="apply checked-in DuckLake and PostgreSQL control-plane SQL",
    )

    bind = commands.add_parser(
        "bind", help="append a transactional PostgreSQL binding override"
    )
    bind.add_argument("namespace")
    bind.add_argument("series_id")
    bind.add_argument("episode")
    target = bind.add_mutually_exclusive_group(required=True)
    target.add_argument("--capture", dest="audio_capture_id")
    target.add_argument("--unbind", action="store_true")
    bind.add_argument("--method", default="human")
    bind.add_argument("--note")

    args = parser.parse_args()
    if args.command == "resolve-sample":
        _resolve_sample(args)
    elif args.command == "resolution-report":
        _resolution_report(args)
    elif args.command == "scan-bronze":
        _scan_bronze(args)
    elif args.command == "bind":
        _bind(args)
    else:
        _apply_lakehouse_schema()


def _resolve_sample(args: argparse.Namespace) -> None:
    if args.limit < 1 or args.limit > 10_000:
        raise SystemExit("--limit must be between 1 and 10000")
    store = bronze_store_from_env()
    provider = AniListEpisodeMetadataProvider()
    documents = tuple(store.scan_documents(limit=args.limit))
    if args.apply:
        with repository_from_env() as repository:
            batch = resolve_batch(
                documents,
                store=store,
                metadata_provider=provider,
                repository=repository,
                run_source="cli:resolve-sample",
            )
            flushed_tables = (
                repository.flush_inlined_data()
                if batch.resolution_write and batch.resolution_write.written
                else 0
            )
    else:
        batch = resolve_batch(
            documents,
            store=store,
            metadata_provider=provider,
        )
        flushed_tables = 0
    results = batch.results
    summary = {
        "mode": "apply" if args.apply else "dry-run",
        "sample_size": len(results),
        "flushed_tables": flushed_tables,
        "bronze_written": bool(batch.bronze_write and batch.bronze_write.written),
        "resolution_written": bool(
            batch.resolution_write and batch.resolution_write.written
        ),
        "classifications": dict(Counter(item.classification for item in results)),
        "reasons": dict(Counter(item.reason for item in results)),
        "issue_reasons_by_series": dict(
            Counter(
                f"{item.series_id}:{item.reason}"
                for item in results
                if item.classification != "accepted"
            )
        ),
    }
    _print_record({"summary": summary}, jsonl=args.jsonl)
    if args.show == "none":
        return
    for result in results:
        if args.show == "issues" and result.classification == "accepted":
            continue
        _print_record(_result_record(result), jsonl=args.jsonl)


def _resolution_report(args: argparse.Namespace) -> None:
    with repository_from_env() as repository:
        _print_record({"summary": repository.summary()}, jsonl=args.jsonl)
        for issue in repository.list_open_issues(limit=args.limit):
            _print_record(asdict(issue), jsonl=args.jsonl)
        for finding in repository.list_consistency_findings()[: args.limit]:
            _print_record(asdict(finding), jsonl=args.jsonl)


def _scan_bronze(args: argparse.Namespace) -> None:
    if args.limit < 1 or args.limit > 100_000:
        raise SystemExit("--limit must be between 1 and 100000")
    with repository_from_env() as repository:
        result = hydrate_bronze_captures(
            bronze_store_from_env(), repository, limit=args.limit
        )
    _print_record({"summary": asdict(result)}, jsonl=False)


def _apply_lakehouse_schema() -> None:
    """Apply lakehouse and control-plane DDL through production paths."""

    import psycopg

    from ja_media_data.binding_overrides import (
        apply_postgres_schema,
        control_schema_from_env,
        postgres_url_for_psycopg,
    )
    from ja_media_data.lakehouse import CatalogConfig, apply_schema, connect_catalog

    config = CatalogConfig.from_env()
    control_schema = control_schema_from_env(catalog_schema=config.metadata_schema)
    with connect_catalog(config) as connection, psycopg.connect(
        postgres_url_for_psycopg(config.postgres_url), autocommit=True
    ) as control_connection:
        lakehouse = apply_schema(connection)
        postgres = apply_postgres_schema(
            control_connection, control_schema=control_schema
        )
    _print_record(
        {
            "summary": {
                "lakehouse": lakehouse,
                "postgres_control": postgres,
                "count": len(lakehouse) + len(postgres),
            }
        },
        jsonl=False,
    )


def _bind(args: argparse.Namespace) -> None:
    """Apply one operator decision and report its effective current binding."""

    with repository_from_env() as repository:
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
    _print_record(
        {
            "summary": {
                "override_id": override_id,
                "locator": f"{args.namespace}:{args.series_id}:{args.episode}",
                "audio_capture_id": (
                    current.audio_capture_id if current is not None else None
                ),
                "status": "bound" if current is not None else "unbound",
            }
        },
        jsonl=False,
    )


def _result_record(result: ResolutionResult) -> dict[str, Any]:
    record = asdict(result)
    if result.classification == "accepted":
        record.pop("evidence")
    return record


def _print_record(record: dict[str, Any], *, jsonl: bool) -> None:
    if jsonl:
        print(json.dumps(record, ensure_ascii=False, sort_keys=True))
        return
    if "summary" in record:
        print(json.dumps(record["summary"], ensure_ascii=False, indent=2, sort_keys=True))
        return
    if "classification" not in record:
        print(json.dumps(record, ensure_ascii=False, sort_keys=True))
        return
    print(
        "\t".join(
            str(record.get(field, ""))
            for field in ("classification", "kind", "reason", "series_id", "stem")
        )
    )
