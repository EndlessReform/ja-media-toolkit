"""Durable cache-key projections for current and historical campaign views."""

from __future__ import annotations

from ja_media_data.lakehouse.repository import DuckLakeRepository
from ja_media_data.phase_d import binding_override_revision
from ja_media_data.pipeline_repository import PipelineRepository


class CampaignKeys:
    """Read compact state vectors without touching stage product rows."""

    def __init__(self, repository: DuckLakeRepository) -> None:
        self.repository = repository
        self._current_token: tuple[object, ...] | None = None

    def product_head(self, target: str) -> tuple[object, ...]:
        """Return the workspace token used by a stage-owned projection.

        ``target`` keeps independently cached stage pages distinct. The
        snapshot itself invalidates every page affected by a lakehouse commit.
        """

        return (target, *self.campaign_state())

    def run_view(self, run_id: str | None) -> tuple[int, int] | None:
        if run_id is None:
            return None
        row = self.repository.connection.execute(
            """SELECT terminal_snapshot_id, override_revision
               FROM pipeline_runs WHERE run_id = ?""",
            [run_id],
        ).fetchone()
        if row is None or row[0] is None:
            raise KeyError(run_id)
        return int(row[0]), int(row[1])

    def campaign_state(self) -> tuple[object, ...]:
        """Return the two authoritative heads for campaign read models.

        A DuckLake snapshot advances for every committed lakehouse transaction,
        including product and checkpoint metadata. The PostgreSQL control
        revision advances for every effective human binding decision. Together
        they provide exact invalidation without scanning product histories or
        relying on a guessed TTL.
        """

        if self._current_token is not None:
            return self._current_token
        snapshot = PipelineRepository(self.repository.connection).current_snapshot_id()
        self._current_token = (
            "snapshot", snapshot,
            "override", binding_override_revision(self.repository),
        )
        return self._current_token
