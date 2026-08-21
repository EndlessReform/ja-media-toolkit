"""Curated campaign run history sourced exclusively from Dagster."""

from __future__ import annotations

from ja_media_data.operator.models import RunPage, RunStepSummary, RunSummary
from ja_media_data.orchestration.dagster.gateway import DagsterGateway, RunFact
from ja_media_data.storage.worker_handoffs import WorkerHandoffReader
from ja_media_data.lakehouse.repository import DuckLakeRepository


class RunHistoryReader:
    """Translate public Dagster facts without duplicating its execution ledger."""

    def __init__(
        self, repository: DuckLakeRepository, gateway: DagsterGateway, *, job_name: str
    ) -> None:
        self.repository = repository
        self.gateway = gateway
        self.job_name = job_name

    def list(self, *, offset: int, limit: int) -> RunPage:
        facts, total = self.gateway.list_runs(
            job_name=self.job_name, offset=offset, limit=limit
        )
        return RunPage(
            items=tuple(self._summary(item) for item in facts),
            total=total,
            offset=offset,
            limit=limit,
        )

    def get(self, run_id: str) -> RunSummary:
        return self._summary(self.gateway.get_run(run_id))

    def _summary(self, run: RunFact) -> RunSummary:
        duration = None
        if run.finished_at:
            duration = max(
                0, round((run.finished_at - run.started_at).total_seconds() * 1000)
            )
        progress = WorkerHandoffReader(self.repository.connection).progress(run.run_id)
        return RunSummary(
            run_id=run.run_id,
            run_number=run.run_number,
            target=run.job_name,
            status=run.status,
            started_at=run.started_at,
            finished_at=run.finished_at,
            terminal_snapshot_id=run.snapshot_id,
            duration_ms=duration,
            error=run.error,
            dagster_url=run.url,
            items_succeeded=progress.succeeded if progress else None,
            items_total=progress.total if progress else None,
            steps=tuple(
                RunStepSummary(
                    stage=step.step_key,
                    ordinal=index,
                    disposition=step.status,
                    started_at=step.started_at,
                    finished_at=step.finished_at,
                    error=step.error,
                )
                for index, step in enumerate(run.steps, start=1)
            ),
        )
