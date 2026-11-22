from __future__ import annotations

from pathlib import Path
from typing import Any

from rich.console import Console

from ja_media_frontend.srt_cleaning.batch import read_jsonl, write_jsonl
from ja_media_frontend.srt_cleaning.reconstruct import reconstruct_from_batch
from ja_media_frontend.srt_cleaning.result_parser import parse_batch_result_row


def reconstruct_provider_output(
    *,
    manifest_path: Path,
    output_path: Path,
    request_ids: set[str],
    limited: bool,
    console: Console,
) -> Path:
    """Reconstruct a hosted-provider run immediately after it completes."""

    manifest_rows = read_jsonl(manifest_path)
    if limited:
        manifest_rows = [
            row for row in manifest_rows if str(row.get("custom_id", "")) in request_ids
        ]

    suffix = ".smoke-reconstruct" if limited else ".reconstruct"
    output_dir = output_path.with_name(f"{_result_stem(output_path)}{suffix}")
    reconstruction_manifest = output_dir / "manifest.jsonl"
    write_jsonl(reconstruction_manifest, manifest_rows)
    summary = reconstruct_from_batch(
        batch_output_paths=[output_path],
        manifest_path=reconstruction_manifest,
        output_dir=output_dir,
    )
    label = "Smoke reconstruction" if limited else "Reconstruction"
    console.print(
        f"[green]{label}:[/] [cyan]{output_dir}[/] "
        f"({summary.cleaned_srts} cleaned, {summary.skipped_sources} withheld, "
        f"{summary.errors} errors)"
    )
    return output_dir


def reusable_result_rows(
    rows: list[dict[str, Any]], manifests: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Keep one locally valid result per request when resuming a hosted run."""

    reusable: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        custom_id = str(row.get("custom_id", ""))
        if custom_id in seen:
            continue
        if "result" not in parse_batch_result_row(row, manifests=manifests):
            continue
        reusable.append(row)
        seen.add(custom_id)
    return reusable


def _result_stem(path: Path) -> str:
    name = path.name
    if name.endswith(".results.jsonl"):
        return name[: -len(".results.jsonl")]
    if name.endswith(".jsonl"):
        return name[: -len(".jsonl")]
    return name
