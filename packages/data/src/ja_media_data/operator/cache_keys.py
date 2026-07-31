"""Exact cache identities from domain heads and public Dagster cursors."""

from __future__ import annotations

from ja_media_data.lakehouse.repository import DuckLakeRepository
from ja_media_data.orchestration.dagster.gateway import DagsterGateway
from ja_media_data.products.canonical_inputs.compiler import binding_override_revision
from ja_media_data.products.lineage import MaterializationCatalog


class CampaignKeys:
    """Read compact revision vectors without loading any product page rows."""

    def __init__(
        self, repository: DuckLakeRepository, gateway: DagsterGateway, *, job_name: str
    ) -> None:
        self.repository = repository
        self.gateway = gateway
        self.job_name = job_name
        self._current_token: tuple[object, ...] | None = None

    def product_head(self, target: str) -> tuple[object, ...]:
        return (target, *self.domain_state())

    def run_view(self, run_id: str | None) -> tuple[int, int] | None:
        """Resolve historical state from Dagster event metadata and domain lineage."""

        if run_id is None:
            return None
        run = self.gateway.get_run(run_id)
        if run.snapshot_id is None:
            raise KeyError(run_id)
        head = MaterializationCatalog(self.repository.connection).current_head(
            "canonical_inputs", snapshot_id=run.snapshot_id
        )
        revision = _override_revision(head.input_heads if head else {})
        return run.snapshot_id, revision

    def domain_state(self) -> tuple[object, ...]:
        """Return exact product and decision revisions for bounded domain views."""

        if self._current_token is None:
            snapshot = MaterializationCatalog(
                self.repository.connection
            ).current_snapshot_id()
            self._current_token = (
                "snapshot",
                snapshot,
                "override",
                binding_override_revision(self.repository),
            )
        return self._current_token

    def campaign_state(self) -> tuple[object, ...]:
        """Add Dagster's latest run cursor only where the spine is rendered."""

        return (
            *self.domain_state(),
            "dagster",
            self.gateway.cursor(job_name=self.job_name),
        )


def _override_revision(heads: object) -> int:
    if isinstance(heads, dict):
        item = heads.get("binding_overrides")
        if isinstance(item, dict) and item.get("revision") is not None:
            return int(item["revision"])
    return 0
