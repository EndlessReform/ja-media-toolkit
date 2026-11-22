from __future__ import annotations

import json
from pathlib import Path
import shutil
from typing import Any, Iterable

from ja_media_frontend.srt_cleaning.contracts import SourceDocument, sha256_text


def frozen_sources(manifest_path: Path, destination: Path) -> list[tuple[SourceDocument, str]]:
    """Load the exact cached SRTs named by a prior window manifest."""

    rows = _read_rows(manifest_path)
    unique: dict[tuple[int, str, str], dict[str, Any]] = {}
    for row in rows:
        key = (int(row["anilist_id"]), str(row["subtitle_id"]), str(row["source_sha256"]))
        unique.setdefault(key, row)

    loaded: list[tuple[SourceDocument, str]] = []
    destination.mkdir(parents=True, exist_ok=True)
    for row in unique.values():
        source_path = Path(str(row["local_cache_path"])).expanduser().resolve()
        # Path.read_text enables universal-newline conversion, which would turn
        # cached CRLF subtitles into different inputs before the hash check.
        source_text = source_path.read_bytes().decode("utf-8-sig")
        source_sha = sha256_text(source_text)
        if source_sha != str(row["source_sha256"]):
            raise ValueError(f"Cached source hash changed: {source_path}")
        copied_path = destination / source_path.name
        if copied_path.resolve() != source_path:
            shutil.copy2(source_path, copied_path)
        loaded.append(
            (
                SourceDocument(
                    anilist_id=int(row["anilist_id"]),
                    subtitle_id=str(row["subtitle_id"]),
                    repo_path=str(row["repo_path"]),
                    filename=str(row["filename"]),
                    source_path=copied_path,
                    metadata_warnings=tuple(row.get("metadata_warnings", ())),
                ),
                source_text,
            )
        )
    return loaded


def downloaded_sources(
    *,
    anilist_id: int,
    inventory: Any,
    subtitle_client: Any,
    destination: Path,
) -> Iterable[tuple[SourceDocument, str]]:
    """Download inventory SRTs into the run's source cache."""

    destination.mkdir(parents=True, exist_ok=True)
    for entry in inventory.entries:
        if not entry.is_srt:
            continue
        source_text = subtitle_client.file_content(entry.subtitle_id).decode("utf-8-sig")
        source_sha = sha256_text(source_text)
        source_path = destination / f"{entry.subtitle_id}.{source_sha[:12]}.srt"
        source_path.write_text(source_text, encoding="utf-8")
        yield (
            SourceDocument(
                anilist_id=anilist_id,
                subtitle_id=entry.subtitle_id,
                repo_path=entry.repo_path,
                filename=entry.name,
                source_path=source_path,
            ),
            source_text,
        )


def _read_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if stripped := line.strip():
                rows.append(json.loads(stripped))
    return rows
