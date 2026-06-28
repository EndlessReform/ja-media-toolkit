from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from ja_media_core.transcripts import SubtitleCue, parse_srt

from ja_media_frontend.srt_cleaning.contracts import (
    DEFAULT_MAX_BYTES_PER_SHARD,
    DEFAULT_MAX_REQUESTS_PER_SHARD,
    OPENAI_CHAT_COMPLETIONS_URL,
    PIPELINE_VERSION,
    BatchShard,
    CleanWindowResult,
    CueWindow,
    SourceDocument,
    sha256_text,
)


def build_windows(
    source: SourceDocument,
    source_text: str,
    *,
    window_cues: int,
    context_cues: int,
    prompt_policy_sha256: str,
) -> list[CueWindow]:
    """Split one SRT into deterministic non-overlapping cleaning windows."""

    if window_cues < 1:
        raise ValueError("window_cues must be at least 1")
    if context_cues < 0:
        raise ValueError("context_cues must not be negative")

    cues = parse_srt(source_text, source_path=source.source_path)
    source_sha = sha256_text(source_text)
    windows: list[CueWindow] = []
    for offset in range(0, len(cues), window_cues):
        active = tuple(cues[offset : offset + window_cues])
        if not active:
            continue
        before_start = max(0, offset - context_cues)
        after_end = min(len(cues), offset + window_cues + context_cues)
        windows.append(
            CueWindow(
                source=source,
                window_number=len(windows) + 1,
                active=active,
                before=tuple(cues[before_start:offset]),
                after=tuple(cues[offset + window_cues : after_end]),
                source_sha256=source_sha,
                prompt_policy_sha256=prompt_policy_sha256,
            )
        )
    return windows


def build_manifest_row(window: CueWindow, *, model: str) -> dict[str, Any]:
    """Return the durable local manifest contract for one window."""

    source = window.source
    return {
        "custom_id": window.custom_id,
        "pipeline_version": PIPELINE_VERSION,
        "anilist_id": source.anilist_id,
        "subtitle_id": source.subtitle_id,
        "repo_path": source.repo_path,
        "filename": source.filename,
        "source_sha256": window.source_sha256,
        "cue_start_index": window.cue_start_index,
        "cue_end_index": window.cue_end_index,
        "active_indexes": list(window.active_indexes),
        "window_number": window.window_number,
        "model": model,
        "prompt_policy_sha256": window.prompt_policy_sha256,
        "local_cache_path": str(source.source_path),
        "metadata_warnings": list(source.metadata_warnings),
    }


def build_batch_row(
    window: CueWindow,
    *,
    model: str,
    policy_text: str,
    series_context: str,
) -> dict[str, Any]:
    """Return one OpenAI-compatible chat-completions batch request."""

    return {
        "custom_id": window.custom_id,
        "method": "POST",
        "url": OPENAI_CHAT_COMPLETIONS_URL,
        "body": {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": policy_text,
                },
                {
                    "role": "user",
                    "content": render_window_prompt(window, series_context=series_context),
                },
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "clean_window_result",
                    "strict": True,
                    "schema": CleanWindowResult.model_json_schema(),
                },
            },
        },
    }


def render_window_prompt(window: CueWindow, *, series_context: str) -> str:
    """Render cue context in a stable, line-oriented prompt format."""

    sections = [series_context.strip(), "Active cues are the only cues to decide."]
    if window.before:
        sections.append("Before context:\n" + render_cues(window.before))
    sections.append("Active cues:\n" + render_cues(window.active))
    if window.after:
        sections.append("After context:\n" + render_cues(window.after))
    return "\n\n".join(section for section in sections if section)


def render_cues(cues: Iterable[SubtitleCue]) -> str:
    lines: list[str] = []
    for cue in cues:
        lines.append(
            f"[{cue.index}] {cue.start_s:.3f} --> {cue.end_s:.3f}\n{cue.text}"
        )
    return "\n\n".join(lines)


def write_generation_artifacts(
    rows: Iterable[dict[str, Any]],
    manifest_rows: Iterable[dict[str, Any]],
    *,
    output_prefix: Path,
    max_requests_per_shard: int = DEFAULT_MAX_REQUESTS_PER_SHARD,
    max_bytes_per_shard: int = DEFAULT_MAX_BYTES_PER_SHARD,
) -> list[BatchShard]:
    """Write manifest JSONL and size-bounded batch request shards."""

    output_prefix.parent.mkdir(parents=True, exist_ok=True)
    manifest_path = prefix_artifact_path(output_prefix, ".manifest.jsonl")
    write_jsonl(manifest_path, manifest_rows)
    return write_batch_shards(
        rows,
        output_prefix=output_prefix,
        max_requests_per_shard=max_requests_per_shard,
        max_bytes_per_shard=max_bytes_per_shard,
    )


def write_batch_shards(
    rows: Iterable[dict[str, Any]],
    *,
    output_prefix: Path,
    max_requests_per_shard: int,
    max_bytes_per_shard: int,
) -> list[BatchShard]:
    """Write JSONL shards without exceeding request or byte limits."""

    if max_requests_per_shard < 1:
        raise ValueError("max_requests_per_shard must be at least 1")
    if max_bytes_per_shard < 1:
        raise ValueError("max_bytes_per_shard must be at least 1")

    shards: list[BatchShard] = []
    current_file = None
    current_path: Path | None = None
    current_count = 0
    current_bytes = 0
    shard_number = 0

    def close_current() -> None:
        nonlocal current_file, current_count, current_bytes, current_path
        if current_file is None or current_path is None:
            return
        current_file.close()
        shards.append(
            BatchShard(
                path=current_path,
                request_count=current_count,
                byte_size=current_bytes,
            )
        )
        current_file = None
        current_path = None

    for row in rows:
        encoded = encode_jsonl_row(row)
        if len(encoded) > max_bytes_per_shard:
            raise ValueError(
                f"single request {row.get('custom_id')} exceeds shard byte limit"
            )
        needs_new = (
            current_file is None
            or current_count >= max_requests_per_shard
            or current_bytes + len(encoded) > max_bytes_per_shard
        )
        if needs_new:
            close_current()
            shard_number += 1
            current_path = prefix_artifact_path(
                output_prefix,
                f".batch-{shard_number:05d}.jsonl",
            )
            current_file = current_path.open("wb")
            current_count = 0
            current_bytes = 0
        assert current_file is not None
        current_file.write(encoded)
        current_count += 1
        current_bytes += len(encoded)

    close_current()
    return shards


def write_shards_summary(path: Path, shards: list[BatchShard], *, model: str) -> None:
    payload = {
        "model": model,
        "endpoint": OPENAI_CHAT_COMPLETIONS_URL,
        "shards": [
            {
                "path": str(shard.path),
                "request_count": shard.request_count,
                "byte_size": shard.byte_size,
            }
            for shard in shards
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def prefix_artifact_path(output_prefix: Path, suffix: str) -> Path:
    """Append an artifact suffix without treating prefix dots as extensions."""

    return output_prefix.with_name(output_prefix.name + suffix)


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        for row in rows:
            handle.write(encode_jsonl_row(row))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            row = json.loads(stripped)
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{line_number} is not a JSON object")
            rows.append(row)
    return rows


def encode_jsonl_row(row: dict[str, Any]) -> bytes:
    return (json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )
