"""Durable local tables and summaries for the identity baseline."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import duckdb


def write_identity_results(
    root: Path,
    *,
    dataset: Path,
    manifest: dict[str, Any],
    results: list[dict[str, object]],
    workers: int,
    elapsed_s: float,
    survey_version: str,
) -> None:
    """Write pair rows, aggregate grains, interchange files, and provenance."""

    jsonl = root / "identity-pairs.jsonl"
    jsonl.write_text("".join(json.dumps(row) + "\n" for row in results))
    database = root / "gate1-identity.duckdb"
    connection = duckdb.connect(str(database))
    try:
        connection.execute(
            "CREATE TABLE identity_pair_scores AS SELECT * FROM read_json_auto(?)",
            [str(jsonl)],
        )
        connection.execute(
            f"ATTACH {_sql_string(str(dataset / 'evaluation.duckdb'))} "
            "AS phase0 (READ_ONLY)"
        )
        connection.execute(
            """CREATE TABLE episode_inventory AS
               SELECT cast(anilist_id AS BIGINT) AS anilist_id,
                      cast(episode AS INTEGER) AS episode FROM phase0.episodes"""
        )
        _derived_tables(connection)
        _copy(connection, "identity_pair_scores", root / "identity-pairs")
        _copy(connection, "identity_episode_best", root / "identity-episodes")
        _copy(connection, "identity_series_summary", root / "identity-series")
        summary = _summary(
            connection,
            manifest=manifest,
            workers=workers,
            elapsed_s=elapsed_s,
            survey_version=survey_version,
        )
    finally:
        connection.close()
    (root / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n"
    )


def _derived_tables(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(
        """CREATE TABLE identity_episode_best AS
           SELECT anilist_id, episode, max(anchor_fit_score) AS best_score,
                  count(*) AS scored_pairs, count(DISTINCT anchor_id) AS anchors,
                  count(DISTINCT candidate_id) AS candidates,
                  bool_or(at_x_hint) AS has_at_x
             FROM identity_pair_scores WHERE status = 'scored'
            GROUP BY anilist_id, episode"""
    )
    connection.execute(
        """CREATE TABLE identity_series_summary AS
           WITH totals AS (
             SELECT anilist_id, count(*) AS total_episodes
               FROM episode_inventory GROUP BY anilist_id
           ), scored AS (
             SELECT anilist_id, count(*) AS scored_episodes,
                    median(best_score) AS median_best_score,
                    quantile_cont(best_score, 0.1) AS p10_best_score,
                    quantile_cont(best_score, 0.9) AS p90_best_score
               FROM identity_episode_best GROUP BY anilist_id
           )
           SELECT totals.anilist_id, totals.total_episodes,
                  coalesce(scored.scored_episodes, 0) AS scored_episodes,
                  coalesce(scored.scored_episodes, 0) / totals.total_episodes
                    AS comparable_fraction,
                  scored.median_best_score, scored.p10_best_score,
                  scored.p90_best_score
             FROM totals LEFT JOIN scored USING (anilist_id)
            ORDER BY totals.anilist_id"""
    )


def _summary(
    connection: duckdb.DuckDBPyConnection,
    *,
    manifest: dict[str, Any],
    workers: int,
    elapsed_s: float,
    survey_version: str,
) -> dict[str, object]:
    total_pairs, scored_pairs, failed_pairs = connection.execute(
        """SELECT count(*), count(*) FILTER (status = 'scored'),
                  count(*) FILTER (status != 'scored')
             FROM identity_pair_scores"""
    ).fetchone()
    pair_quantiles = _quantiles(connection, "identity_pair_scores", "anchor_fit_score", "status = 'scored'")
    episode_quantiles = _quantiles(connection, "identity_episode_best", "best_score", "true")
    scored_episodes = connection.execute(
        "SELECT count(*) FROM identity_episode_best"
    ).fetchone()[0]
    pair_episodes = connection.execute(
        """SELECT count(*) FROM (
             SELECT anilist_id, episode FROM identity_pair_scores GROUP BY 1, 2
           )"""
    ).fetchone()[0]
    total_episodes = connection.execute(
        "SELECT count(*) FROM episode_inventory"
    ).fetchone()[0]
    failure_tracks = connection.execute(
        """SELECT
             count(DISTINCT cast(anchor_id AS VARCHAR))
               FILTER (failure_side = 'anchor'),
             count(DISTINCT cast(candidate_id AS VARCHAR))
               FILTER (failure_side = 'candidate')
           FROM identity_pair_scores WHERE status != 'scored'"""
    ).fetchone()
    multiplicity = connection.execute(
        """SELECT count(*) FILTER (anchors > 1),
                  count(*) FILTER (candidates > 1)
             FROM identity_episode_best"""
    ).fetchone()
    at_x = connection.execute(
        """SELECT count(*) FILTER (at_x_hint),
                  median(anchor_fit_score) FILTER (at_x_hint),
                  median(anchor_fit_score) FILTER (NOT at_x_hint)
             FROM identity_pair_scores WHERE status = 'scored'"""
    ).fetchone()
    zero_pairs = connection.execute(
        """SELECT count(*) FROM identity_pair_scores
            WHERE status = 'scored' AND anchor_fit_score = 0"""
    ).fetchone()[0]
    return {
        "survey_version": survey_version,
        "dataset_id": manifest["dataset_id"],
        "silver": manifest["silver"],
        "workers": workers,
        "elapsed_seconds": elapsed_s,
        "total_episodes": total_episodes,
        "episodes_with_pairs": pair_episodes,
        "episodes_with_scored_pairs": scored_episodes,
        "episodes_without_pairs": total_episodes - pair_episodes,
        "episodes_blocked_by_parsing": pair_episodes - scored_episodes,
        "total_pairs": total_pairs,
        "scored_pairs": scored_pairs,
        "failed_pairs": failed_pairs,
        "failed_anchor_tracks": failure_tracks[0],
        "failed_candidate_tracks": failure_tracks[1],
        "multi_anchor_episodes": multiplicity[0],
        "multi_candidate_episodes": multiplicity[1],
        "zero_score_pairs": zero_pairs,
        "pair_score_quantiles": pair_quantiles,
        "episode_best_quantiles": episode_quantiles,
        "at_x_scored_pairs": at_x[0],
        "at_x_pair_median": at_x[1],
        "other_pair_median": at_x[2],
        "scorers": ["subtitle_goodness_of_fit", "subtitle_anchor_fit_score"],
        "timing_transform": "identity",
    }


def _quantiles(
    connection: duckdb.DuckDBPyConnection,
    table: str,
    column: str,
    predicate: str,
) -> list[float]:
    return connection.execute(
        f"SELECT quantile_cont({column}, [0.1,0.25,0.5,0.75,0.9]) "
        f"FROM {table} WHERE {predicate}"
    ).fetchone()[0]


def _copy(connection: duckdb.DuckDBPyConnection, table: str, stem: Path) -> None:
    connection.execute(
        f"COPY {table} TO {_sql_string(str(stem.with_suffix('.csv')))} "
        "(FORMAT CSV, HEADER)"
    )
    connection.execute(
        f"COPY {table} TO {_sql_string(str(stem.with_suffix('.parquet')))} "
        "(FORMAT PARQUET)"
    )


def _sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"
