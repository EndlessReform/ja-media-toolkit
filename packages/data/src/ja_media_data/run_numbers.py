"""Transactional allocation of human-facing global run numbers."""

from __future__ import annotations

from datetime import UTC, datetime
import socket
import uuid

import duckdb


def create_pipeline_run(
    connection: duckdb.DuckDBPyConnection,
    target: str,
    *,
    forced_from_stage: str | None,
    override_revision: int,
) -> str:
    """Allocate the next Run # and create its UUID-backed durable record."""

    run_id = f"run-{uuid.uuid4().hex}"
    connection.execute("BEGIN TRANSACTION")
    try:
        run_number = int(connection.execute(
            "SELECT next_number FROM pipeline_run_counter"
        ).fetchone()[0])
        connection.execute(
            "UPDATE pipeline_run_counter SET next_number = next_number + 1"
        )
        connection.execute(
            """INSERT INTO pipeline_runs (
                   run_id, target, forced_from_stage, status, machine,
                   started_at, finished_at, terminal_snapshot_id,
                   override_revision, error_message, run_number
               ) VALUES (?, ?, ?, 'running', ?, ?, NULL, NULL, ?, NULL, ?)""",
            [run_id, target, forced_from_stage, socket.gethostname(),
             datetime.now(UTC), override_revision, run_number],
        )
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    return run_id
