"""Deterministic identity-decile sampling and self-contained pair staging."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import os
from pathlib import Path
import shutil

import duckdb


@dataclass(frozen=True)
class MatrixPair:
    """One anchor/candidate pair selected for every method arm."""

    pair_id: str
    anilist_id: int
    episode: int
    anchor_id: str
    candidate_id: str
    anchor_format: str
    candidate_format: str
    anchor_source: str
    candidate_source: str
    candidate_repo_path: str
    identity_decile: int
    identity_score: float
    identity_goodness: float


def select_pairs(
    dataset: Path, identity_result: Path, *, sample_size: int
) -> list[MatrixPair]:
    """Select episode-best identity pairs, then score-stratify that cohort."""

    buckets = min(10, sample_size)
    per_bucket = (sample_size + buckets - 1) // buckets
    connection = duckdb.connect(
        str(identity_result / "gate1-identity.duckdb"), read_only=True
    )
    try:
        connection.execute(
            f"ATTACH {_sql_string(str(dataset / 'evaluation.duckdb'))} "
            "AS phase0 (READ_ONLY)"
        )
        rows = connection.execute(
            f"""WITH scored AS (
                  SELECT score.*,
                         hash(concat(anchor_id, cast(candidate_id AS VARCHAR)))
                           AS tie_break
                    FROM identity_pair_scores AS score
                   WHERE status = 'scored'
                ), episode_best AS (
                  SELECT *, row_number() OVER (
                           PARTITION BY anilist_id, episode
                           ORDER BY anchor_fit_score DESC,
                                    goodness_of_fit DESC NULLS LAST,
                                    tie_break
                         ) AS identity_rank
                    FROM scored
                ), bucketed AS (
                  SELECT episode_best.*,
                         ntile({buckets}) OVER (ORDER BY anchor_fit_score)
                           AS identity_decile
                    FROM episode_best
                   WHERE identity_rank = 1
                ), ranked AS (
                  SELECT *, row_number() OVER (
                           PARTITION BY identity_decile ORDER BY tie_break
                         ) AS sample_rank
                    FROM bucketed
                )
                SELECT ranked.anilist_id, ranked.episode, ranked.anchor_id,
                       cast(ranked.candidate_id AS VARCHAR),
                       ranked.anchor_serialization,
                       ranked.candidate_extension,
                       anchor.relative_path, candidate.relative_path,
                       ranked.candidate_repo_path, ranked.identity_decile,
                       ranked.anchor_fit_score, ranked.goodness_of_fit
                  FROM ranked
                  JOIN phase0.embedded_subtitles AS anchor
                    ON anchor.subtitle_input_id = ranked.anchor_id
                  JOIN phase0.kitsunekko_candidates AS candidate
                    ON candidate.subtitle_id = cast(ranked.candidate_id AS VARCHAR)
                 WHERE sample_rank <= {per_bucket}
                 ORDER BY sample_rank, identity_decile, tie_break
                 LIMIT {sample_size}"""
        ).fetchall()
    finally:
        connection.close()
    return [
        MatrixPair(
            pair_id=_pair_id(str(row[2]), str(row[3])),
            anilist_id=int(row[0]), episode=int(row[1]),
            anchor_id=str(row[2]), candidate_id=str(row[3]),
            anchor_format=str(row[4]), candidate_format=str(row[5]),
            anchor_source=str(row[6]), candidate_source=str(row[7]),
            candidate_repo_path=str(row[8]), identity_decile=int(row[9]),
            identity_score=float(row[10]), identity_goodness=float(row[11]),
        )
        for row in rows
    ]


def stage_pair(dataset: Path, root: Path, pair: MatrixPair) -> tuple[Path, Path]:
    """Hard-link immutable inputs beside outputs for the future reviewer."""

    pair_root = root / "artifacts" / pair.pair_id
    pair_root.mkdir(parents=True, exist_ok=True)
    anchor = pair_root / f"anchor.{_extension(pair.anchor_format)}"
    candidate = pair_root / f"candidate.{_extension(pair.candidate_format)}"
    _link_or_copy(dataset / pair.anchor_source, anchor)
    _link_or_copy(dataset / pair.candidate_source, candidate)
    return anchor, candidate


def _link_or_copy(source: Path, target: Path) -> None:
    if target.exists():
        return
    try:
        os.link(source, target)
    except OSError:
        shutil.copyfile(source, target)


def _extension(format_name: str) -> str:
    return "srt" if format_name.casefold() == "subrip" else format_name.casefold()


def _pair_id(anchor_id: str, candidate_id: str) -> str:
    return sha256(f"{anchor_id}\0{candidate_id}".encode()).hexdigest()[:20]


def _sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"
