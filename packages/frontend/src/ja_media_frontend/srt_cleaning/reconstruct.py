from __future__ import annotations

import json
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from ja_media_core.transcripts import format_srt, parse_srt

from ja_media_frontend.srt_cleaning.batch import read_jsonl, write_jsonl
from ja_media_frontend.srt_cleaning.result_parser import (
    WindowResult,
    base_window_error,
    parse_batch_result_row,
    to_dlq_row,
)
from ja_media_frontend.srt_cleaning.source_rebuild import (
    apply_decisions,
    cleaned_srt_name,
    collect_source_decisions,
    group_expected_windows,
    has_blocking_source_error,
    render_decision_rows,
    validate_source_windows,
)
from ja_media_frontend.srt_cleaning.workspace import (
    WINDOW_SCHEMA_NAME,
    WINDOW_SCHEMA_VERSION,
    validate_schema_major,
)


@dataclass(frozen=True)
class ReconstructionSummary:
    """Counts and paths produced by one reconstruction run."""

    decisions: int
    errors: int
    dlq: int
    cleaned_srts: int
    skipped_sources: int
    decisions_path: Path
    errors_path: Path
    dlq_path: Path
    archive_path: Path | None


def reconstruct_from_batch(
    *,
    batch_output_paths: Iterable[Path],
    manifest_path: Path,
    output_dir: Path,
    allow_partial: bool = False,
    archive: bool = True,
) -> ReconstructionSummary:
    """Reconstruct cleaned SRTs from unordered OpenAI-style batch results.

    Failed rows and malformed spans are written to a DLQ instead of aborting the
    whole run. A source SRT is emitted only when every expected window for that
    source has a complete, non-conflicting decision set, unless partial output
    is explicitly allowed.
    """

    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_rows = read_jsonl(manifest_path)
    validate_manifest_schemas(manifest_path, manifest_rows)
    manifests = {str(row["custom_id"]): row for row in manifest_rows}
    expected_by_source = group_expected_windows(manifest_rows)

    errors: list[dict[str, Any]] = []
    dlq: list[dict[str, Any]] = []
    window_results: dict[str, WindowResult] = {}

    for batch_path in batch_output_paths:
        for line_number, row in iter_jsonl_rows(batch_path, errors):
            parsed = parse_batch_result_row(row, manifests=manifests)
            if "error" in parsed:
                error = {
                    "batch_output_path": str(batch_path),
                    "line_number": line_number,
                    **parsed["error"],
                }
                errors.append(error)
                dlq.append(to_dlq_row(error, manifests.get(str(row.get("custom_id", "")))))
                continue
            result = parsed["result"]
            if result.custom_id in window_results:
                error = base_window_error(
                    result.custom_id,
                    "duplicate_result",
                    "Batch output contains more than one result for this window.",
                    manifests[result.custom_id],
                )
                error["batch_output_path"] = str(batch_path)
                error["line_number"] = line_number
                errors.append(error)
                dlq.append(to_dlq_row(error, manifests[result.custom_id]))
                continue
            window_results[result.custom_id] = result

    decisions_rows: list[dict[str, Any]] = []
    cleaned_count = 0
    skipped_count = 0
    clean_dir = output_dir / "cleaned"
    clean_dir.mkdir(exist_ok=True)

    for source_key, source_manifests in expected_by_source.items():
        decisions_rows.extend(
            render_decision_rows(source_key, source_manifests, window_results)
        )
        source_errors = validate_source_windows(source_manifests, window_results)
        if source_errors:
            errors.extend(source_errors)
            dlq.extend(
                to_dlq_row(error, manifests.get(error.get("custom_id", "")))
                for error in source_errors
            )
            if not allow_partial:
                skipped_count += 1
                continue

        source_path = Path(str(source_manifests[0]["local_cache_path"]))
        cues = parse_srt(source_path.read_text(encoding="utf-8-sig"), source_path=source_path)
        decisions_by_index = collect_source_decisions(
            source_manifests,
            window_results,
            errors=errors,
            dlq=dlq,
        )
        if has_blocking_source_error(source_manifests, errors) and not allow_partial:
            skipped_count += 1
            continue
        cleaned = apply_decisions(cues, decisions_by_index)
        output_path = clean_dir / cleaned_srt_name(source_manifests[0])
        output_path.write_text(format_srt(cleaned), encoding="utf-8")
        cleaned_count += 1

    decisions_path = output_dir / "decisions.jsonl"
    errors_path = output_dir / "errors.jsonl"
    dlq_path = output_dir / "dlq.jsonl"
    write_jsonl(decisions_path, decisions_rows)
    write_jsonl(errors_path, errors)
    write_jsonl(dlq_path, dlq)

    archive_path = None
    if archive:
        archive_path = output_dir / "cleaned-srts.tar.gz"
        with tarfile.open(archive_path, "w:gz") as tar:
            for srt_path in sorted(clean_dir.glob("*.srt")):
                tar.add(srt_path, arcname=srt_path.name)

    return ReconstructionSummary(
        decisions=len(decisions_rows),
        errors=len(errors),
        dlq=len(dlq),
        cleaned_srts=cleaned_count,
        skipped_sources=skipped_count,
        decisions_path=decisions_path,
        errors_path=errors_path,
        dlq_path=dlq_path,
        archive_path=archive_path,
    )


def validate_manifest_schemas(
    manifest_path: Path,
    manifest_rows: list[dict[str, Any]],
) -> None:
    """Reject future incompatible manifest row schemas."""

    for row in manifest_rows:
        validate_schema_major(
            schema_name=row.get("schema_name"),
            schema_version=row.get("schema_version"),
            expected_name=WINDOW_SCHEMA_NAME,
            expected_version=WINDOW_SCHEMA_VERSION,
            artifact=manifest_path,
        )


def iter_jsonl_rows(
    path: Path,
    errors: list[dict[str, Any]],
) -> Iterable[tuple[int, dict[str, Any]]]:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                row = json.loads(stripped)
            except json.JSONDecodeError as exc:
                errors.append(
                    {
                        "batch_output_path": str(path),
                        "line_number": line_number,
                        "error_kind": "invalid_jsonl",
                        "message": str(exc),
                        "retryable": False,
                    }
                )
                continue
            if not isinstance(row, dict):
                errors.append(
                    {
                        "batch_output_path": str(path),
                        "line_number": line_number,
                        "error_kind": "invalid_jsonl",
                        "message": "JSONL row is not an object.",
                        "retryable": False,
                    }
                )
                continue
            yield line_number, row
