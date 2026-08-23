from __future__ import annotations

import json
from pathlib import Path

from forced_alignment_retiming.slice import discover_cleaned_slice


def test_discovers_unique_sources_and_preserves_catalog_aliases(tmp_path: Path) -> None:
    reconstruct = tmp_path / "reconstruct"
    reconstruct.mkdir()
    rows = [
        _row("sub-b", "b" * 64, 57),
        _row("sub-a", "a" * 64, 72),
        _row("sub-c", "a" * 64, 72),
    ]
    (reconstruct / "manifest.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )

    result = discover_cleaned_slice(reconstruct, episode=1)

    assert [item.case.name for item in result] == [
        "anilist-72-e01-aaaaaaaaaaaa",
        "anilist-57-e01-bbbbbbbbbbbb",
    ]
    assert result[0].case.subtitle_id == "sub-a"
    assert result[0].catalog_subtitle_ids == ("sub-a", "sub-c")


def _row(subtitle_id: str, source_hash: str, series_id: int) -> dict[str, str]:
    return {
        "subtitle_id": subtitle_id,
        "source_sha256": source_hash,
        "custom_id": f"clean:v2:anilist-{series_id}:srt-{subtitle_id}:w00001",
    }
