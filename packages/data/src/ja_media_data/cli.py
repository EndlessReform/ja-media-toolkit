"""Local developer commands for dry-running and inspecting episode resolution."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
import json
from typing import Any

from ja_media_data.bronze import bronze_store_from_env, ledger_repository_from_env
from ja_media_data.database import create_ledger_engine, create_session_factory
from ja_media_data.diagnostics import ResolutionDiagnostics
from ja_media_data.episode_metadata import AniListEpisodeMetadataProvider
from ja_media_data.resolution_service import ResolutionResult, resolve_document


def main() -> None:
    """Run the small, deliberately developer-facing resolver CLI."""

    parser = argparse.ArgumentParser(prog="ja-media-data")
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
        "resolution-report", help="inspect current ledger counts and open issues"
    )
    report.add_argument("--limit", type=int, default=100)
    report.add_argument("--jsonl", action="store_true")

    args = parser.parse_args()
    if args.command == "resolve-sample":
        _resolve_sample(args)
    else:
        _resolution_report(args)


def _resolve_sample(args: argparse.Namespace) -> None:
    if args.limit < 1 or args.limit > 10_000:
        raise SystemExit("--limit must be between 1 and 10000")
    store = bronze_store_from_env()
    provider = AniListEpisodeMetadataProvider()
    repository = ledger_repository_from_env() if args.apply else None
    results = [
        resolve_document(
            document,
            store=store,
            metadata_provider=provider,
            repository=repository,
        )
        for document in store.scan_documents(limit=args.limit)
    ]
    summary = {
        "mode": "apply" if args.apply else "dry-run",
        "sample_size": len(results),
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
    engine = create_ledger_engine()
    diagnostics = ResolutionDiagnostics(create_session_factory(engine))
    _print_record({"summary": diagnostics.summary()}, jsonl=args.jsonl)
    for issue in diagnostics.open_issues(limit=args.limit):
        _print_record(asdict(issue), jsonl=args.jsonl)
    engine.dispose()


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
    print(
        "\t".join(
            str(record.get(field, ""))
            for field in ("classification", "kind", "reason", "series_id", "stem")
        )
    )
