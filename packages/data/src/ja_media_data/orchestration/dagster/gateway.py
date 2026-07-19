"""Supported read gateway from the operator application to Dagster.

The gateway uses public ``DagsterInstance`` methods.  No operator route knows
Dagster's PostgreSQL schema or executes SQL against run/event storage.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import os
from pathlib import Path
from typing import Mapping

import dagster as dg


class DagsterGatewayError(RuntimeError):
    """Base for errors safe to translate at the application boundary."""


class DagsterRunNotFound(DagsterGatewayError, KeyError):
    """The requested Dagster run does not exist."""


class DagsterUnavailable(DagsterGatewayError):
    """The configured Dagster instance cannot be queried."""


@dataclass(frozen=True)
class StepFact:
    """One Dagster step execution projected from durable events."""

    step_key: str
    status: str
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None = None


@dataclass(frozen=True)
class RunFact:
    """Bounded public run record used by the operator projections."""

    run_id: str
    run_number: int
    job_name: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    tags: Mapping[str, str]
    steps: tuple[StepFact, ...]
    error: str | None
    snapshot_id: int | None
    url: str


class DagsterGateway:
    """FastAPI-lifetime facade over public Dagster instance read methods."""

    def __init__(
        self, instance: dg.DagsterInstance, *, ui_url: str = "http://127.0.0.1:53000",
        owns_instance: bool = False,
    ) -> None:
        self.instance = instance
        self.ui_url = ui_url.rstrip("/")
        self._owns_instance = owns_instance

    @classmethod
    def from_env(cls) -> DagsterGateway:
        """Open the configured instance once without exposing its storage layout."""

        config_dir = os.environ.get("JA_MEDIA_DAGSTER_HOME")
        if config_dir is None:
            config_dir = str(Path(__file__).parents[4] / "dagster")
        try:
            instance = dg.DagsterInstance.from_config(config_dir)
        except Exception as error:
            raise DagsterUnavailable(f"cannot open Dagster instance: {error}") from error
        return cls(
            instance,
            ui_url=os.environ.get(
                "JA_MEDIA_DAGSTER_UI_URL", "http://127.0.0.1:53000"
            ),
            owns_instance=True,
        )

    def list_runs(
        self, *, job_name: str, offset: int = 0, limit: int = 50
    ) -> tuple[tuple[RunFact, ...], int]:
        """Return a bounded newest-first page and total for one campaign job."""

        filters = dg.RunsFilter(job_name=job_name)
        records = self.instance.get_run_records(filters=filters, limit=offset + limit)
        total = self.instance.get_runs_count(filters=filters)
        return tuple(self._fact(item) for item in records[offset:]), total

    def get_run(self, run_id: str) -> RunFact:
        record = self.instance.get_run_record_by_id(run_id)
        if record is None:
            raise DagsterRunNotFound(run_id)
        return self._fact(record)

    def latest_steps(self, *, job_name: str) -> dict[str, tuple[RunFact, StepFact]]:
        """Return the newest execution fact for every observed step key."""

        facts, _ = self.list_runs(job_name=job_name, limit=100)
        latest: dict[str, tuple[RunFact, StepFact]] = {}
        for run in facts:
            for step in run.steps:
                latest.setdefault(step.step_key, (run, step))
        return latest

    def cursor(self, *, job_name: str) -> tuple[int, str] | None:
        """Return a compact invalidation token without reading event tables."""

        records = self.instance.get_run_records(
            filters=dg.RunsFilter(job_name=job_name), limit=1
        )
        if not records:
            return None
        item = records[0]
        return int(item.storage_id), item.dagster_run.status.value

    def close(self) -> None:
        if self._owns_instance:
            self.instance.dispose()

    def _fact(self, record: object) -> RunFact:
        run = record.dagster_run
        logs = self.instance.all_logs(run.run_id)
        steps = _steps(logs)
        start = record.start_time or record.create_timestamp.timestamp()
        failure = next(
            (event.message for event in reversed(logs)
             if event.dagster_event_type == dg.DagsterEventType.RUN_FAILURE),
            None,
        )
        snapshots = [
            value for event in logs
            if event.dagster_event_type == dg.DagsterEventType.ASSET_MATERIALIZATION
            for value in [_snapshot_value(event)] if value is not None
        ]
        return RunFact(
            run_id=run.run_id, run_number=int(record.storage_id),
            job_name=run.job_name, status=_run_status(run.status),
            started_at=datetime.fromtimestamp(start, UTC),
            finished_at=(datetime.fromtimestamp(record.end_time, UTC)
                         if record.end_time else None),
            tags=dict(run.tags), steps=steps, error=failure,
            snapshot_id=max(snapshots) if snapshots else None,
            url=f"{self.ui_url}/runs/{run.run_id}",
        )


def _steps(logs: object) -> tuple[StepFact, ...]:
    values: dict[str, dict[str, object]] = {}
    order: list[str] = []
    for event in logs:
        key = event.step_key
        if not key:
            continue
        if key not in values:
            values[key] = {"status": "queued", "started": None, "finished": None,
                           "error": None}
            order.append(key)
        value = values[key]
        kind = event.dagster_event_type
        when = datetime.fromtimestamp(event.timestamp, UTC)
        if kind == dg.DagsterEventType.STEP_START:
            value.update(status="started", started=when)
        elif kind == dg.DagsterEventType.STEP_SUCCESS:
            value.update(status="succeeded", finished=when)
        elif kind == dg.DagsterEventType.STEP_FAILURE:
            value.update(status="failed", finished=when, error=event.message)
    return tuple(
        StepFact(key, str(values[key]["status"]), values[key]["started"],
                 values[key]["finished"], values[key]["error"])
        for key in order
    )


def _snapshot_value(event: object) -> int | None:
    materialization = event.dagster_event.event_specific_data.materialization
    item = materialization.metadata.get("ducklake_snapshot_id")
    value = getattr(item, "value", None)
    return int(value) if value is not None else None


def _run_status(status: dg.DagsterRunStatus) -> str:
    return {
        dg.DagsterRunStatus.SUCCESS: "succeeded",
        dg.DagsterRunStatus.FAILURE: "failed",
        dg.DagsterRunStatus.STARTED: "started",
        dg.DagsterRunStatus.STARTING: "started",
        dg.DagsterRunStatus.QUEUED: "queued",
        dg.DagsterRunStatus.CANCELED: "canceled",
        dg.DagsterRunStatus.CANCELING: "canceling",
    }.get(status, status.value.lower())
