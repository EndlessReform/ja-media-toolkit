"""Bulk, dependency-aware observations for the pipeline spine."""

from ja_media_data.lakehouse.repository import DuckLakeRepository
from ja_media_data.operator.currency import evaluate_currency
from ja_media_data.operator.models import StageObservation
from ja_media_data.operator.stage_contracts import CANONICALIZATION_SPINE
from ja_media_data.pipeline_repository import PipelineRepository
from ja_media_data.lakehouse.time_travel import table_ref


def observe_stages(
    repository: DuckLakeRepository, *, snapshot_id: int | None = None,
    override_revision: int | None = None,
) -> tuple[StageObservation, ...]:
    """Return committed heads, latest attempts, and currency as separate facts."""

    counts = _counts(repository, snapshot_id=snapshot_id)
    latest = _latest_checkpoints(repository, snapshot_id=snapshot_id)
    producers = _producing_runs(repository, snapshot_id=snapshot_id)
    pipeline = PipelineRepository(repository.connection)
    observations = []
    for contract in CANONICALIZATION_SPINE:
        head = pipeline.current_head(contract.stage, snapshot_id=snapshot_id)
        currency, stale_reason = evaluate_currency(
            repository, contract, head, snapshot_id=snapshot_id,
            override_revision=override_revision,
        )
        recent = latest.get(contract.stage)
        producer = producers.get(head.materialization_id) if head else None
        duration = None
        if recent and recent[4] and recent[5]:
            duration = max(0, round((recent[5] - recent[4]).total_seconds() * 1000))
        observations.append(StageObservation(
            stage=contract.stage, label=contract.label,
            status="materialized" if head else "not_materialized",
            input_rows=counts[contract.input_table],
            output_rows=counts[contract.output_table],
            fingerprint=head.fingerprint if head else None,
            materialization_id=head.materialization_id if head else None,
            snapshot_id=head.snapshot_id if head else None,
            materialized_at=head.computed_at if head else None,
            output_run_id=str(producer[0]) if producer else None,
            output_run_number=int(producer[2]) if producer and producer[2] else None,
            output_attempt_id=head.run_id if head else None,
            currency=currency, stale_reason=stale_reason,
            latest_run_id=str(recent[0]) if recent else None,
            latest_run_number=int(recent[7]) if recent and recent[7] else None,
            latest_attempt_id=str(recent[1]) if recent and recent[1] else None,
            latest_run_status=str(recent[2]) if recent else None,
            latest_run_started_at=recent[4] if recent else None,
            latest_run_duration_ms=duration,
            latest_run_error=str(recent[6]) if recent and recent[6] else None,
        ))
    return tuple(observations)


def _counts(
    repository: DuckLakeRepository, *, snapshot_id: int | None
) -> dict[str, int]:
    tables = tuple(dict.fromkeys(
        name for stage in CANONICALIZATION_SPINE
        for name in (stage.input_table, stage.output_table)
    ))
    select = ", ".join(
        f"(SELECT count(*) FROM {table_ref(name, snapshot_id=snapshot_id)}) AS {name}"
        for name in tables
    )
    row = repository.connection.execute("SELECT " + select).fetchone()
    return {name: int(row[index]) for index, name in enumerate(tables)}


def _latest_checkpoints(
    repository: DuckLakeRepository, *, snapshot_id: int | None
) -> dict[str, tuple[object, ...]]:
    source = table_ref(
        "run_stage_checkpoints", snapshot_id=snapshot_id, alias="checkpoint"
    )
    rows = repository.connection.execute(
        """SELECT checkpoint.run_id, checkpoint.attempt_id,
                  checkpoint.disposition, checkpoint.stage,
                  checkpoint.started_at, checkpoint.finished_at,
                  checkpoint.error_message, run.run_number
           FROM """ + source + """
           LEFT JOIN pipeline_runs AS run ON run.run_id = checkpoint.run_id
           QUALIFY row_number() OVER (
               PARTITION BY checkpoint.stage
               ORDER BY coalesce(checkpoint.started_at, checkpoint.finished_at) DESC,
                        checkpoint.checkpoint_id DESC
           ) = 1"""
    ).fetchall()
    return {str(row[3]): row for row in rows}


def _producing_runs(
    repository: DuckLakeRepository, *, snapshot_id: int | None
) -> dict[str, tuple[object, ...]]:
    source = table_ref(
        "run_stage_checkpoints", snapshot_id=snapshot_id, alias="checkpoint"
    )
    rows = repository.connection.execute(
        """SELECT checkpoint.materialization_id, checkpoint.run_id,
                  checkpoint.attempt_id, run.run_number
           FROM """ + source + """
           LEFT JOIN pipeline_runs AS run ON run.run_id = checkpoint.run_id
           WHERE checkpoint.materialization_id IS NOT NULL
           QUALIFY row_number() OVER (
               PARTITION BY checkpoint.materialization_id
               ORDER BY checkpoint.finished_at DESC
           ) = 1"""
    ).fetchall()
    return {str(row[0]): row[1:] for row in rows}
