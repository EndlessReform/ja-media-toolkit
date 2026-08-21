"""Bounded reads over compacted worker handoff outcomes."""

from __future__ import annotations

from dataclasses import dataclass

import duckdb


@dataclass(frozen=True)
class HandoffProgress:
    """Truthful per-item progress independent of the enclosing run status."""

    succeeded: int
    total: int


class WorkerHandoffReader:
    """Query compacted handoffs without loading request/result documents."""

    def __init__(self, connection: duckdb.DuckDBPyConnection) -> None:
        self.connection = connection

    def progress(self, campaign_run_id: str) -> HandoffProgress | None:
        row = self.connection.execute(
            """SELECT count(*) FILTER (WHERE disposition = 'succeeded'), count(*)
               FROM worker_handoff_items WHERE campaign_run_id = ?""",
            [campaign_run_id],
        ).fetchone()
        if row is None or int(row[1]) == 0:
            return None
        return HandoffProgress(succeeded=int(row[0]), total=int(row[1]))
