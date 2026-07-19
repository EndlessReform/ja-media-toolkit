"""Curated Dagster run projections; raw logs remain in the Dagster UI."""

from datetime import datetime

from ja_media_data.operator.models.base import OperatorModel


class StageCheckpointSummary(OperatorModel):
    """One Dagster step execution within a campaign run."""

    stage: str
    ordinal: int
    disposition: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None


class RunSummary(OperatorModel):
    """One Dagster campaign run decorated for operator navigation."""

    run_id: str
    run_number: int
    target: str
    status: str
    started_at: datetime
    finished_at: datetime | None = None
    terminal_snapshot_id: int | None = None
    duration_ms: int | None = None
    error: str | None = None
    checkpoints: tuple[StageCheckpointSummary, ...] = ()
    dagster_url: str
    items_succeeded: int | None = None
    items_total: int | None = None


class RunPage(OperatorModel):
    items: tuple[RunSummary, ...]
    total: int
    offset: int
    limit: int
