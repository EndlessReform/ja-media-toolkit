"""Join Dagster execution facts to bounded domain-product observations."""

from __future__ import annotations

from ja_media_data.lakehouse.repository import DuckLakeRepository
from ja_media_data.operator.models import StageObservation
from ja_media_data.operator.product_currency import evaluate_currency
from ja_media_data.orchestration.dagster.gateway import (
    DagsterGateway,
    DagsterRunNotFound,
)
from ja_media_data.orchestration.dagster.presentation import StagePresentation
from ja_media_data.products.lineage import MaterializationCatalog
from ja_media_data.lakehouse.time_travel import table_ref


def observe_stages(
    repository: DuckLakeRepository,
    gateway: DagsterGateway,
    spine: tuple[StagePresentation, ...],
    *,
    job_name: str,
    snapshot_id: int | None = None,
    override_revision: int | None = None,
) -> tuple[StageObservation, ...]:
    """Return product heads and Dagster attempts as deliberately separate facts."""

    counts = _counts(repository, spine, snapshot_id=snapshot_id)
    latest = gateway.latest_steps(job_name=job_name)
    catalog = MaterializationCatalog(repository.connection)
    observations: list[StageObservation] = []
    for item in spine:
        head = catalog.current_head(item.domain_target, snapshot_id=snapshot_id)
        currency, stale_reason = evaluate_currency(
            repository, item, head, snapshot_id=snapshot_id,
            override_revision=override_revision,
        )
        producer = _producer(gateway, head.run_id if head else None)
        execution = latest.get(item.op_name)
        run, step = execution if execution else (None, None)
        duration = None
        if step and step.started_at and step.finished_at:
            duration = max(
                0, round((step.finished_at - step.started_at).total_seconds() * 1000)
            )
        observations.append(StageObservation(
            stage=item.stage, step_key=item.op_name, label=item.label,
            status="materialized" if head else "not_materialized",
            input_rows=counts[item.input_table], output_rows=counts[item.output_table],
            fingerprint=head.fingerprint if head else None,
            materialization_id=head.materialization_id if head else None,
            snapshot_id=head.snapshot_id if head else None,
            materialized_at=head.computed_at if head else None,
            output_run_id=producer.run_id if producer else None,
            output_run_number=producer.run_number if producer else None,
            output_attempt_id=head.run_id if head else None,
            currency=currency, stale_reason=stale_reason,
            latest_run_id=run.run_id if run else None,
            latest_run_number=run.run_number if run else None,
            latest_run_status=step.status if step else None,
            latest_run_started_at=step.started_at if step else None,
            latest_run_duration_ms=duration,
            latest_run_error=step.error if step else None,
            dagster_url=run.url if run else None,
        ))
    return tuple(observations)


def dagster_run_id(value: str | None) -> str | None:
    """Extract the execution UUID from domain attempt IDs used during E1."""

    if not value:
        return None
    if value.startswith("dagster:"):
        parts = value.split(":", 2)
        return parts[1] if len(parts) == 3 else None
    return value


def _producer(gateway: DagsterGateway, attempt_id: str | None):
    run_id = dagster_run_id(attempt_id)
    if run_id is None:
        return None
    try:
        return gateway.get_run(run_id)
    except DagsterRunNotFound:
        return None


def _counts(
    repository: DuckLakeRepository,
    spine: tuple[StagePresentation, ...],
    *, snapshot_id: int | None,
) -> dict[str, int]:
    tables = tuple(dict.fromkeys(
        name for item in spine for name in (item.input_table, item.output_table)
    ))
    select = ", ".join(
        f"(SELECT count(*) FROM {table_ref(name, snapshot_id=snapshot_id)}) AS {name}"
        for name in tables
    )
    row = repository.connection.execute("SELECT " + select).fetchone()
    return {name: int(row[index]) for index, name in enumerate(tables)}
