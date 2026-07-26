"""Read the matrix's durable review products without rerunning aligners."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path

import duckdb

from subtitle_alignment.track_io import read_subtitle_cues


@dataclass(frozen=True)
class Variant:
    """One aligned artifact and its measured transform."""

    pair_id: str
    method: str
    method_order: int
    output_path: str
    status: str
    score: float | None
    gain: float | None
    scale: float | None
    median_offset_s: float | None
    min_offset_s: float | None
    max_offset_s: float | None
    max_abs_offset_s: float | None
    offset_blocks: int | None
    offset_bound_exceeded: bool
    block_starts_s: tuple[float, ...]


@dataclass(frozen=True)
class ReviewCase:
    """An anchor/candidate pair with every precomputed method variant."""

    pair_id: str
    anilist_id: int
    episode: int
    anchor_path: str
    anchor_format: str
    candidate_path: str
    candidate_format: str
    candidate_repo_path: str
    identity_decile: int
    variants: tuple[Variant, ...]

    @property
    def flagged(self) -> bool:
        return any(variant.offset_bound_exceeded for variant in self.variants)


@dataclass(frozen=True)
class TrackStats:
    """Simple evidence for whether a track can anchor a whole episode."""

    cues: int
    active_s: float
    first_s: float
    last_s: float

    @property
    def span_s(self) -> float:
        return max(0.0, self.last_s - self.first_s)

    @property
    def active_fraction(self) -> float:
        return self.active_s / self.span_s if self.span_s else 0.0


def load_review_cases(result: Path, *, flagged_only: bool = False) -> list[ReviewCase]:
    """Load pair-method rows, deriving the flag for legacy matrix-v2 runs."""

    database = result / "gate1-matrix.duckdb"
    connection = duckdb.connect(str(database), read_only=True)
    try:
        columns = {
            row[0] for row in connection.execute("DESCRIBE review_variants").fetchall()
        }
        flag = (
            "coalesce(offset_bound_exceeded, false)"
            if "offset_bound_exceeded" in columns
            else "tool = 'alass' AND coalesce(max_abs_offset_s, 0) > 30"
        )
        cursor = connection.execute(
            f"""SELECT pair_id, anilist_id, episode, anchor_path, anchor_format,
                       candidate_path, candidate_format, candidate_repo_path,
                       identity_decile, method, method_order, output_path, status,
                       anchor_fit_score, gain_over_identity, scale,
                       median_offset_s, min_offset_s, max_offset_s,
                       max_abs_offset_s, offset_blocks, {flag} AS offset_flag
                  FROM review_variants
              ORDER BY pair_id, method_order"""
        )
        names = [item[0] for item in cursor.description]
        rows = [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]
        boundaries = _load_boundaries(connection)
    finally:
        connection.close()
    grouped: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        grouped.setdefault(str(row["pair_id"]), []).append(row)
    cases = [_case(group, boundaries) for group in grouped.values()]
    cases.sort(key=lambda case: (not case.flagged, case.identity_decile, case.pair_id))
    return [case for case in cases if case.flagged or not flagged_only]


def load_tracks(result: Path, case: ReviewCase, variant: Variant):
    """Parse the anchor, original candidate, and selected staged output."""

    anchor = read_subtitle_cues(result / case.anchor_path, case.anchor_format)
    candidate = read_subtitle_cues(result / case.candidate_path, case.candidate_format)
    output = read_subtitle_cues(result / variant.output_path, case.candidate_format)
    return anchor, candidate, output


def track_stats(cues) -> TrackStats:
    """Summarize cue density without pretending it proves semantic coverage."""

    if not cues:
        return TrackStats(0, 0.0, 0.0, 0.0)
    return TrackStats(
        len(cues),
        sum(max(0.0, cue.end_s - cue.start_s) for cue in cues),
        min(cue.start_s for cue in cues),
        max(cue.end_s for cue in cues),
    )


def append_judgment(path: Path, case: ReviewCase, variant: Variant, label: str) -> None:
    """Append a diagnostic judgment; source subtitles are never modified."""

    payload = {
        "recorded_at": datetime.now(UTC).isoformat(),
        "pair_id": case.pair_id,
        "method": variant.method,
        "label": label,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(payload, sort_keys=True) + "\n")


def _load_boundaries(connection) -> dict[tuple[str, str], tuple[float, ...]]:
    rows = connection.execute(
        """SELECT pair_id, method, list(aligned_start_s ORDER BY cue_index)
             FILTER (offset_block_start) AS starts
             FROM cue_transforms GROUP BY pair_id, method"""
    ).fetchall()
    return {(pair_id, method): tuple(starts or ()) for pair_id, method, starts in rows}


def _case(rows, boundaries) -> ReviewCase:
    first = rows[0]
    variants = tuple(
        Variant(
            pair_id=str(row["pair_id"]), method=str(row["method"]),
            method_order=int(row["method_order"]), output_path=str(row["output_path"]),
            status=str(row["status"]), score=row["anchor_fit_score"],
            gain=row["gain_over_identity"], scale=row["scale"],
            median_offset_s=row["median_offset_s"], min_offset_s=row["min_offset_s"],
            max_offset_s=row["max_offset_s"], max_abs_offset_s=row["max_abs_offset_s"],
            offset_blocks=row["offset_blocks"],
            offset_bound_exceeded=bool(row["offset_flag"]),
            block_starts_s=boundaries.get((str(row["pair_id"]), str(row["method"])), ()),
        )
        for row in rows
    )
    return ReviewCase(
        pair_id=str(first["pair_id"]), anilist_id=int(first["anilist_id"]),
        episode=int(first["episode"]), anchor_path=str(first["anchor_path"]),
        anchor_format=str(first["anchor_format"]),
        candidate_path=str(first["candidate_path"]),
        candidate_format=str(first["candidate_format"]),
        candidate_repo_path=str(first["candidate_repo_path"]),
        identity_decile=int(first["identity_decile"]), variants=variants,
    )
