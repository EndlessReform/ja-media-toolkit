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
    """Select episode-best pairs across score strata, preferring new series."""

    buckets = min(10, sample_size)
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
                )
                SELECT bucketed.anilist_id, bucketed.episode, bucketed.anchor_id,
                       cast(bucketed.candidate_id AS VARCHAR),
                       bucketed.anchor_serialization,
                       bucketed.candidate_extension,
                       anchor.relative_path, candidate.relative_path,
                       bucketed.candidate_repo_path, bucketed.identity_decile,
                       bucketed.anchor_fit_score, bucketed.goodness_of_fit
                  FROM bucketed
                  JOIN phase0.embedded_subtitles AS anchor
                    ON anchor.subtitle_input_id = bucketed.anchor_id
                  JOIN phase0.kitsunekko_candidates AS candidate
                    ON candidate.subtitle_id = cast(bucketed.candidate_id AS VARCHAR)
                 ORDER BY identity_decile, tie_break"""
        ).fetchall()
    finally:
        connection.close()
    pairs = [
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
    return _diverse_stratified_sample(pairs, sample_size, buckets)


def _diverse_stratified_sample(
    pairs: list[MatrixPair], sample_size: int, buckets: int
) -> list[MatrixPair]:
    """Maximize series coverage, then fill remaining slots across score buckets.

    The first pass skips a bucket rather than spend a slot on a repeated series
    while any unseen series remains elsewhere. Both that pass and the fallback
    retain round-robin traversal of identity-score strata.
    """

    pools = {
        bucket: [pair for pair in pairs if pair.identity_decile == bucket]
        for bucket in range(1, buckets + 1)
    }
    selected: list[MatrixPair] = []
    seen_series: set[int] = set()
    while len(selected) < sample_size:
        added = False
        for bucket in range(1, buckets + 1):
            pool = pools[bucket]
            if not pool or len(selected) == sample_size:
                continue
            index = next(
                (i for i, pair in enumerate(pool) if pair.anilist_id not in seen_series),
                None,
            )
            if index is None:
                continue
            pair = pool.pop(index)
            selected.append(pair)
            seen_series.add(pair.anilist_id)
            added = True
        if not added:
            break
    while len(selected) < sample_size and any(pools.values()):
        for bucket in range(1, buckets + 1):
            if pools[bucket] and len(selected) < sample_size:
                selected.append(pools[bucket].pop(0))
    return selected


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
