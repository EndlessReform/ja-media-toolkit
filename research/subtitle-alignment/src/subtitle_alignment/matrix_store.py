"""DuckDB, interchange tables, and UI projections for the method matrix."""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
from typing import Any

import duckdb

from subtitle_alignment.matrix_methods import MethodSpec, definition_rows
from subtitle_alignment.matrix_sample import MatrixPair


def write_matrix_results(
    root: Path,
    *,
    manifest: dict[str, Any],
    pairs: list[MatrixPair],
    specs: tuple[MethodSpec, ...],
    runs: list[dict[str, object]],
    versions: dict[str, str],
    workers: int,
    elapsed_s: float,
    matrix_version: str,
) -> None:
    """Persist normalized experiment, reporting, and annotator-facing grains."""

    pair_rows = []
    for pair in pairs:
        row = asdict(pair)
        pair_root = Path("artifacts") / pair.pair_id
        row["anchor_path"] = str(
            pair_root / f"anchor.{_extension(pair.anchor_format)}"
        )
        row["candidate_path"] = str(
            pair_root / f"candidate.{_extension(pair.candidate_format)}"
        )
        pair_rows.append(row)
    _jsonl(root / "pair-sample.jsonl", pair_rows)
    _jsonl(root / "method-definitions.jsonl", definition_rows(specs))
    _jsonl(
        root / "method-runs.jsonl",
        sorted(runs, key=lambda row: (str(row["pair_id"]), str(row["method"]))),
    )
    database = root / "gate1-matrix.duckdb"
    connection = duckdb.connect(str(database))
    try:
        _load_sources(connection, root)
        _derived_tables(connection)
        for table, filename in (
            ("pair_sample", "pair-sample"),
            ("method_definitions", "method-definitions"),
            ("method_runs", "method-runs"),
            ("method_summary", "method-summary"),
            ("transform_summary", "transform-summary"),
            ("review_queue", "review-queue"),
            ("review_variants", "review-variants"),
        ):
            _copy(connection, table, root / filename, csv=True)
        _copy(connection, "cue_transforms", root / "cue-transforms", csv=False)
        summary = _summary(
            connection,
            manifest=manifest,
            versions=versions,
            workers=workers,
            elapsed_s=elapsed_s,
            matrix_version=matrix_version,
        )
    finally:
        connection.close()
    (root / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n"
    )


def _load_sources(connection: duckdb.DuckDBPyConnection, root: Path) -> None:
    for table, source in (
        ("pair_sample", root / "pair-sample.jsonl"),
        ("method_definitions", root / "method-definitions.jsonl"),
        ("method_runs", root / "method-runs.jsonl"),
    ):
        connection.execute(
            f"CREATE TABLE {table} AS SELECT * FROM read_json_auto(?)", [str(source)]
        )
    connection.execute(
        "CREATE TABLE cue_transforms AS SELECT * FROM read_json_auto(?)",
        [str(root / "artifacts" / "*" / "cue-transforms.jsonl")],
    )


def _derived_tables(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(
        """CREATE TABLE method_summary AS
           WITH best AS (
             SELECT pair_id, max(anchor_fit_score) AS best_score
               FROM method_runs WHERE status LIKE 'scored%' GROUP BY pair_id
           )
           SELECT definition.method_order, definition.key AS method,
                  definition.tool, definition.transform_class,
                  definition.scale_enabled, definition.piecewise_enabled,
                  definition.split_penalty, count(*) AS attempted_pairs,
                  count(*) FILTER (run.status LIKE 'scored%') AS scored_pairs,
                  count(*) FILTER (run.status = 'scored_transform_error')
                    AS transform_errors,
                  count(*) FILTER (run.status = 'bounded_reject') AS bounded_rejects,
                  count(*) FILTER (
                    run.status NOT LIKE 'scored%' AND run.status != 'bounded_reject'
                  )
                    AS failed_pairs,
                  median(run.anchor_fit_score) FILTER (run.status LIKE 'scored%')
                    AS median_score,
                  median(run.gain_over_identity) FILTER (run.status LIKE 'scored%')
                    AS median_gain,
                  avg((run.gain_over_identity > 0)::INTEGER)
                    FILTER (run.status LIKE 'scored%') AS improved_fraction,
                  avg((abs(run.anchor_fit_score - best.best_score) < 1e-9)::INTEGER)
                    FILTER (run.status LIKE 'scored%') AS win_fraction,
                  median(run.runtime_ms) FILTER (run.status LIKE 'scored%')
                    AS median_runtime_ms,
                  quantile_cont(run.runtime_ms, 0.9)
                    FILTER (run.status LIKE 'scored%')
                    AS p90_runtime_ms,
                  median(run.offset_blocks) FILTER (run.status LIKE 'scored%')
                    AS median_offset_blocks,
                  avg((abs(run.scale - 1) > 0.0005)::INTEGER)
                    FILTER (run.status LIKE 'scored%') AS scaled_fraction,
                  max(run.max_abs_offset_s) FILTER (run.status LIKE 'scored%')
                    AS max_abs_offset_s
             FROM method_definitions AS definition
             JOIN method_runs AS run ON run.method = definition.key
             LEFT JOIN best USING (pair_id)
            GROUP BY ALL ORDER BY definition.method_order"""
    )
    connection.execute(
        """CREATE TABLE transform_summary AS
           SELECT transform_class, count(DISTINCT method) AS methods,
                  count(*) FILTER (status LIKE 'scored%') AS scored_runs,
                  median(anchor_fit_score) FILTER (status LIKE 'scored%') AS median_score,
                  median(gain_over_identity) FILTER (status LIKE 'scored%') AS median_gain,
                  median(runtime_ms) FILTER (status LIKE 'scored%') AS median_runtime_ms,
                  median(offset_blocks) FILTER (status LIKE 'scored%') AS median_blocks
             FROM method_runs GROUP BY transform_class
            ORDER BY CASE transform_class WHEN 'identity' THEN 0 WHEN 'global' THEN 1
                     WHEN 'clock+global' THEN 2 ELSE 3 END"""
    )
    connection.execute(
        """CREATE TABLE review_queue AS
           SELECT pair.*, arg_max(run.method, run.anchor_fit_score)
                    FILTER (run.status LIKE 'scored%') AS best_method,
                  max(run.anchor_fit_score) FILTER (run.status LIKE 'scored%') AS best_score,
                  max(run.anchor_fit_score) FILTER (run.method = 'identity')
                    AS identity_score_check,
                  max(run.gain_over_identity) FILTER (run.status LIKE 'scored%')
                    AS best_gain,
                  max(run.anchor_fit_score) FILTER (run.status LIKE 'scored%') -
                    min(run.anchor_fit_score) FILTER (run.status LIKE 'scored%')
                    AS method_score_spread,
                  count(*) FILTER (run.status LIKE 'scored%') AS scored_methods
             FROM pair_sample AS pair JOIN method_runs AS run USING (pair_id)
            GROUP BY ALL ORDER BY method_score_spread DESC, pair_id"""
    )
    connection.execute(
        """CREATE TABLE review_variants AS
           SELECT pair.anilist_id, pair.episode, pair.anchor_id,
                  pair.candidate_id, pair.candidate_repo_path,
                  pair.anchor_format, pair.candidate_format,
                  pair.identity_decile, pair.identity_score,
                  pair.anchor_path, pair.candidate_path,
                  definition.method_order, definition.args AS method_args,
                  run.*
             FROM pair_sample AS pair
             JOIN method_runs AS run USING (pair_id)
             JOIN method_definitions AS definition ON definition.key = run.method
            ORDER BY pair.pair_id, run.method"""
    )


def _summary(connection, *, manifest, versions, workers, elapsed_s, matrix_version):
    pairs = connection.execute("SELECT count(*) FROM pair_sample").fetchone()[0]
    methods = connection.execute("SELECT count(*) FROM method_definitions").fetchone()[0]
    scored, transform_errors, rejected, failed = connection.execute(
        """SELECT count(*) FILTER (status LIKE 'scored%'),
                  count(*) FILTER (status='scored_transform_error'),
                  count(*) FILTER (status='bounded_reject'),
                  count(*) FILTER (
                    status NOT LIKE 'scored%' AND status != 'bounded_reject'
                  )
             FROM method_runs"""
    ).fetchone()
    best = connection.execute(
        """SELECT method, median_gain FROM method_summary
            WHERE method != 'identity' AND scored_pairs > 0
            ORDER BY median_gain DESC NULLS LAST LIMIT 1"""
    ).fetchone()
    return {
        "matrix_version": matrix_version,
        "dataset_id": manifest["dataset_id"],
        "silver": manifest["silver"],
        "pair_count": pairs,
        "method_count": methods,
        "run_count": pairs * methods,
        "scored_runs": scored,
        "transform_errors": transform_errors,
        "bounded_rejects": rejected,
        "failed_runs": failed,
        "workers": workers,
        "elapsed_seconds": elapsed_s,
        "executable_versions": versions,
        "scorer": "subtitle_anchor_fit_score (ALASS-derived)",
        "best_median_gain_method": best[0] if best else None,
        "best_median_gain": best[1] if best else None,
    }


def _copy(connection, table: str, stem: Path, *, csv: bool) -> None:
    connection.execute(
        f"COPY {table} TO {_sql_string(str(stem.with_suffix('.parquet')))} "
        "(FORMAT PARQUET)"
    )
    if csv:
        connection.execute(
            f"COPY {table} TO {_sql_string(str(stem.with_suffix('.csv')))} "
            "(FORMAT CSV, HEADER)"
        )


def _jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def _extension(format_name: str) -> str:
    return "srt" if format_name.casefold() == "subrip" else format_name.casefold()


def _sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"
