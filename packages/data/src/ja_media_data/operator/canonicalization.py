"""Application service for the canonicalization-gate campaign."""

from __future__ import annotations

from ja_media_data.lakehouse.repository import DuckLakeRepository
from ja_media_data.operator.canonical_product import CanonicalProductProjector
from ja_media_data.operator.models import CanonicalizationLens
from ja_media_data.operator.models import CampaignProgress
from ja_media_data.operator.product_currency import evaluate_currency
from ja_media_data.orchestration.dagster.gateway import DagsterGateway
from ja_media_data.orchestration.dagster.presentation import BY_STAGE, StagePresentation
from ja_media_data.products.lineage import MaterializationCatalog
from ja_media_data.operator.stage_status import observe_stages
from ja_media_data.products.canonical_inputs.compiler import CANONICALIZATION_POLICY_VERSION


class CanonicalizationLensProjector:
    """Compose metadata, one product page, and the current stage spine."""

    def __init__(
        self,
        repository: DuckLakeRepository,
        gateway: DagsterGateway,
        *,
        spine: tuple[StagePresentation, ...],
        job_name: str,
    ) -> None:
        self.repository = repository
        self.gateway = gateway
        self.spine = spine
        self.job_name = job_name
        self.products = CanonicalProductProjector(repository)

    def project(
        self, *, series_id: str | None = None, gate_offset: int = 0,
        gate_limit: int = 50, snapshot_id: int | None = None,
        override_revision: int | None = None, view_run_id: str | None = None,
    ) -> CanonicalizationLens:
        """Build a bounded lens for the current or one historical workspace."""

        stages = observe_stages(
            self.repository, self.gateway, self.spine, job_name=self.job_name,
            snapshot_id=snapshot_id,
            override_revision=override_revision,
        )
        product = next(item for item in stages if item.stage == "canonical_inputs")
        gates, total = self.products.page(
            series_id=series_id, offset=gate_offset, limit=gate_limit,
            currency=product.currency, stale_reason=product.stale_reason,
            snapshot_id=snapshot_id, override_revision=override_revision,
        )
        return CanonicalizationLens(
            policy_version=CANONICALIZATION_POLICY_VERSION,
            progress=self.products.progress(
                series_id=series_id, total_locators=total,
                currency=product.currency, snapshot_id=snapshot_id,
                override_revision=override_revision,
            ),
            stages=stages, gates=gates, gate_total=total,
            gate_offset=gate_offset, gate_limit=gate_limit, issues=(),
            product_materialization_id=product.materialization_id,
            product_run_id=product.output_run_id,
            product_run_number=product.output_run_number,
            product_attempt_id=product.output_attempt_id,
            product_currency=product.currency,
            product_stale_reason=product.stale_reason,
            view_snapshot_id=snapshot_id, view_run_id=view_run_id,
        )

    def candidates(
        self, locator: tuple[str, str, str], *, snapshot_id: int | None = None,
        override_revision: int | None = None,
    ):
        """Load candidate evidence independently from the product page."""

        return self.products.candidates(
            locator, snapshot_id=snapshot_id,
            override_revision=override_revision,
        )

    def project_product(
        self, *, series_id: str | None, gate_offset: int, gate_limit: int,
        snapshot_id: int | None, override_revision: int | None,
        view_run_id: str | None,
    ) -> CanonicalizationLens:
        """Build only the paged conclusion product, without campaign summaries."""

        head = MaterializationCatalog(self.repository.connection).current_head(
            "canonical_inputs", snapshot_id=snapshot_id
        )
        currency, stale_reason = evaluate_currency(
            self.repository, BY_STAGE["canonical_inputs"], head,
            snapshot_id=snapshot_id, override_revision=override_revision,
        )
        gates, total = self.products.page(
            series_id=series_id, offset=gate_offset, limit=gate_limit,
            currency=currency, stale_reason=stale_reason,
            snapshot_id=snapshot_id, override_revision=override_revision,
        )
        producer = None
        if head:
            from ja_media_data.operator.stage_status import dagster_run_id
            from ja_media_data.orchestration.dagster.gateway import DagsterRunNotFound

            try:
                run_id = dagster_run_id(head.run_id)
                producer = self.gateway.get_run(run_id) if run_id else None
            except DagsterRunNotFound:
                producer = None
        return CanonicalizationLens(
            policy_version=CANONICALIZATION_POLICY_VERSION,
            progress=CampaignProgress(
                captures=0, proposals=0, quarantined=0, locators=total,
                canonicalized=0, stale=0, unbound=0,
                awaiting_acceptance=0, awaiting_canonicalization=0,
            ),
            stages=(), gates=gates, gate_total=total, gate_offset=gate_offset,
            gate_limit=gate_limit, issues=(),
            product_materialization_id=head.materialization_id if head else None,
            product_run_id=producer.run_id if producer else None,
            product_run_number=producer.run_number if producer else None,
            product_attempt_id=head.run_id if head else None,
            product_currency=currency, product_stale_reason=stale_reason,
            view_snapshot_id=snapshot_id, view_run_id=view_run_id,
        )
