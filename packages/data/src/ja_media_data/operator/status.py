"""Bulk projections from real Phase D tables into common product observations."""

from __future__ import annotations

from datetime import UTC, datetime

from ja_media_data.lakehouse.repository import DuckLakeRepository
from ja_media_data.operator.products import (
    Currency,
    Execution,
    ProductKey,
    ProductObservation,
    Readiness,
)
from ja_media_data.operator.currency import evaluate_currency
from ja_media_data.operator.stage_contracts import STAGE_BY_NAME
from ja_media_data.pipeline_repository import PipelineRepository


_MATERIALIZATION_TARGET = {
    "accepted_bindings": "accepted_bindings",
    "canonical_inputs": "canonical_inputs",
    "subtitle_lid": "subtitle_lid",
}
_PRODUCT_STAGE = dict(_MATERIALIZATION_TARGET)
_SOURCE_TABLE = {
    "episode_binding_proposals": "episode_binding_proposals",
    "bronze_captures": "bronze_captures",
}
_SOURCE_MATERIALIZATION = {
    "episode_binding_proposals": "episode_resolution",
    "bronze_captures": "bronze_captures",
}


class PhaseDStatusAdapter:
    """Load materializations and latest runs once for a bounded planning snapshot."""

    def __init__(self, repository: DuckLakeRepository) -> None:
        self.repository = repository
        self.observed_at = datetime.now(UTC)
        self._materializations = {
            str(row[0]): (str(row[1]), row[2], str(row[3]) if row[3] else None)
            for row in repository.connection.execute(
                """SELECT target, fingerprint, computed_at, run_id
                   FROM materializations WHERE scope = 'corpus'
                   QUALIFY row_number() OVER (
                       PARTITION BY target ORDER BY computed_at DESC
                   ) = 1"""
            ).fetchall()
        }
        self._runs = {
            str(row[0]): row[1:]
            for row in repository.connection.execute(
                """SELECT stage, disposition, run_id, recipe_revision
                   FROM run_stage_checkpoints
                   QUALIFY row_number() OVER (
                       PARTITION BY stage ORDER BY coalesce(started_at, finished_at) DESC,
                                                checkpoint_id DESC
                   ) = 1"""
            ).fetchall()
        }
        self._source_counts = {
            product: int(repository.connection.execute(
                f"SELECT count(*) FROM {table}"
            ).fetchone()[0])
            for product, table in _SOURCE_TABLE.items()
        }

    def observe(self, key: ProductKey) -> ProductObservation | None:
        """Project one key from the already bulk-loaded snapshot."""

        if key.subject.kind != "corpus":
            return None
        if key.product_type in self._source_counts:
            materialization = self._materializations.get(
                _SOURCE_MATERIALIZATION[key.product_type]
            )
            return ProductObservation(
                key=key,
                currency=Currency.CURRENT,
                readiness=Readiness.READY,
                fingerprint=materialization[0] if materialization else None,
                observed_at=self.observed_at,
                reason=f"{self._source_counts[key.product_type]} source rows",
            )
        target = _MATERIALIZATION_TARGET.get(key.product_type)
        if target is None:
            return None
        materialization = self._materializations.get(target)
        run = self._runs.get(target)
        execution = Execution.IDLE
        run_id = None
        recipe = None
        if run:
            execution = {
                "running": Execution.STARTED,
                "succeeded": Execution.SUCCEEDED,
                "reused": Execution.SUCCEEDED,
                "failed": Execution.FAILED,
            }.get(str(run[0]), Execution.IDLE)
            run_id = str(run[1])
            recipe = str(run[2])
        currency = Currency.MISSING
        reason = "no corpus materialization"
        if materialization:
            head = PipelineRepository(self.repository.connection).current_head(target)
            observed_currency, reason = evaluate_currency(
                self.repository, STAGE_BY_NAME[_PRODUCT_STAGE[key.product_type]], head
            )
            currency = (
                Currency.CURRENT if observed_currency == "current" else Currency.STALE
            )
        return ProductObservation(
            key=key,
            currency=currency,
            readiness=(Readiness.READY if currency != Currency.MISSING else Readiness.BLOCKED_INPUTS),
            execution=execution,
            fingerprint=materialization[0] if materialization else None,
            observed_at=materialization[1] if materialization else self.observed_at,
            run_id=run_id,
            reason=reason,
        )
