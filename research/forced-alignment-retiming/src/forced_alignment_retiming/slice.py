"""Discover the unique cleaned subtitle files in one reconstruction."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re

from forced_alignment_retiming.cases import CanonicalCase


_ANILIST = re.compile(r"(?:^|:)anilist-(?P<series_id>[0-9]+)(?::|$)")


@dataclass(frozen=True)
class CleanedSlice:
    """One source subtitle, collapsed by content hash across catalog aliases."""

    case: CanonicalCase
    catalog_subtitle_ids: tuple[str, ...]


def discover_cleaned_slice(
    reconstruct_dir: Path, *, episode: int
) -> list[CleanedSlice]:
    """Build deterministic cases from a cleaning manifest.

    The cleaning custom ID pins the AniList series. Episode remains an explicit
    campaign input because the cleaning contract does not currently store it.
    Rows sharing a source hash are the same bytes and run only once.
    """

    if episode < 1:
        raise ValueError("episode must be positive")
    manifest_path = reconstruct_dir / "manifest.jsonl"
    rows = [
        json.loads(line)
        for line in manifest_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    grouped: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        source_hash = str(row.get("source_sha256") or "")
        if not source_hash:
            raise ValueError("cleaning manifest row has no source_sha256")
        grouped.setdefault(source_hash, []).append(row)

    discovered = []
    for source_hash, source_rows in sorted(grouped.items()):
        subtitle_ids = sorted({str(row["subtitle_id"]) for row in source_rows})
        series_ids = {_series_id(str(row["custom_id"])) for row in source_rows}
        if len(series_ids) != 1:
            raise ValueError(f"source {source_hash} maps to multiple AniList series")
        series_id = series_ids.pop()
        selected_id = subtitle_ids[0]
        case = CanonicalCase(
            name=f"anilist-{series_id}-e{episode:02d}-{source_hash[:12]}",
            namespace="anilist",
            series_id=series_id,
            episode=str(episode),
            subtitle_id=selected_id,
            subtitle_source_sha256=source_hash,
            cleaning_reconstruct=str(reconstruct_dir),
        )
        discovered.append(
            CleanedSlice(case=case, catalog_subtitle_ids=tuple(subtitle_ids))
        )
    return discovered


def _series_id(custom_id: str) -> str:
    match = _ANILIST.search(custom_id)
    if match is None:
        raise ValueError(f"cleaning custom ID has no AniList series: {custom_id}")
    return match.group("series_id")
