"""Read models for global runs, local checkpoints, and recipe activity."""

from __future__ import annotations

from ja_media_data.lakehouse.repository import DuckLakeRepository
from ja_media_data.operator.models import (
    RunPage,
    RunSummary,
    StageCheckpointSummary,
)


class RunHistoryReader:
    """Project execution metadata without coupling it to campaign composition."""

    def __init__(self, repository: DuckLakeRepository) -> None:
        self.repository = repository

    def list(self, *, offset: int, limit: int) -> RunPage:
        total = int(self.repository.connection.execute(
            "SELECT count(*) FROM pipeline_runs"
        ).fetchone()[0])
        rows = self.repository.connection.execute(
            """SELECT run_id, run_number, target, forced_from_stage, machine, status,
                      started_at, finished_at, terminal_snapshot_id,
                      override_revision, error_message
               FROM pipeline_runs ORDER BY run_number DESC LIMIT ? OFFSET ?""",
            [limit, offset],
        ).fetchall()
        return RunPage(
            items=tuple(self._summary(row) for row in rows), total=total,
            offset=offset, limit=limit,
        )

    def get(self, run_id: str) -> RunSummary:
        row = self.repository.connection.execute(
            """SELECT run_id, run_number, target, forced_from_stage, machine, status,
                      started_at, finished_at, terminal_snapshot_id,
                      override_revision, error_message
               FROM pipeline_runs WHERE run_id = ? LIMIT 1""",
            [run_id],
        ).fetchone()
        if row is None:
            raise KeyError(run_id)
        return self._summary(row)

    def recipe_observations(self) -> dict[str, dict[str, object]]:
        rows = self.repository.connection.execute(
            """SELECT recipe_revision, count(*),
                      count(*) FILTER (WHERE disposition = 'failed'), max(started_at)
               FROM run_stage_checkpoints GROUP BY recipe_revision"""
        ).fetchall()
        current = {
            str(row[0]): int(row[1])
            for row in self.repository.connection.execute(
                """WITH heads AS (
                       SELECT * FROM materializations
                       QUALIFY row_number() OVER (
                           PARTITION BY target ORDER BY computed_at DESC,
                                                      materialization_id DESC
                       ) = 1
                   )
                   SELECT checkpoint.recipe_revision, count(*)
                   FROM heads AS materialization
                   JOIN run_stage_checkpoints AS checkpoint
                     ON checkpoint.attempt_id = materialization.run_id
                   GROUP BY checkpoint.recipe_revision"""
            ).fetchall()
        }
        return {
            str(row[0]): {
                "run_count": int(row[1]), "failure_count": int(row[2]),
                "current_products": current.get(str(row[0]), 0),
                "latest_run_at": row[3],
            } for row in rows
        }

    def _summary(self, row: tuple[object, ...]) -> RunSummary:
        checkpoints = self.repository.connection.execute(
            """SELECT stage, ordinal, disposition, attempt_id, materialization_id,
                      recipe_revision, started_at, finished_at, error_message
               FROM run_stage_checkpoints WHERE run_id = ?
               ORDER BY ordinal, checkpoint_id""",
            [row[0]],
        ).fetchall()
        duration = None
        if row[6] and row[7]:
            duration = max(0, round((row[7] - row[6]).total_seconds() * 1000))
        return RunSummary(
            run_id=str(row[0]), run_number=int(row[1]), target=str(row[2]),
            forced_from_stage=str(row[3]) if row[3] else None,
            machine=str(row[4]), status=str(row[5]), started_at=row[6],
            finished_at=row[7],
            terminal_snapshot_id=int(row[8]) if row[8] is not None else None,
            override_revision=int(row[9]), duration_ms=duration,
            error=str(row[10]) if row[10] else None,
            checkpoints=tuple(StageCheckpointSummary(
                stage=str(item[0]), ordinal=int(item[1]), disposition=str(item[2]),
                attempt_id=str(item[3]) if item[3] else None,
                materialization_id=str(item[4]) if item[4] else None,
                recipe_revision=str(item[5]), started_at=item[6], finished_at=item[7],
                error=str(item[8]) if item[8] else None,
            ) for item in checkpoints),
        )
