"""Surface-neutral operator use cases over Dagster and durable products."""

from __future__ import annotations

from datetime import UTC, datetime

from ja_media_data.lakehouse.repository import DuckLakeRepository
from ja_media_data.operator.cache import ProjectionCache
from ja_media_data.operator.cache_keys import CampaignKeys
from ja_media_data.operator.campaigns import (
    CampaignCatalog,
    CampaignDefinition,
    campaign_card,
)
from ja_media_data.operator.canonicalization import CanonicalizationLensProjector
from ja_media_data.operator.models import (
    CampaignCard,
    CampaignSnapshot,
    RunPage,
    RunSummary,
    StageResultPage,
)
from ja_media_data.operator.run_history import RunHistoryReader
from ja_media_data.operator.stage_results import StageResultProjector
from ja_media_data.orchestration.dagster.definitions import build_definitions
from ja_media_data.orchestration.dagster.gateway import DagsterGateway


class OperatorApplication:
    """Join orchestration facts to bounded domain views without owning execution."""

    def __init__(
        self,
        repository: DuckLakeRepository,
        gateway: DagsterGateway,
        *,
        campaigns: CampaignCatalog | None = None,
        cache: ProjectionCache | None = None,
    ) -> None:
        self.repository = repository
        self.gateway = gateway
        self.campaigns = campaigns or CampaignCatalog(build_definitions())
        self.cache = cache if cache is not None else ProjectionCache()
        self._stage_results = StageResultProjector(repository)

    def list_campaigns(self) -> tuple[CampaignCard, ...]:
        return tuple(campaign_card(item) for item in self.campaigns.all())

    def get_campaign_snapshot(
        self,
        campaign_id: str,
        *,
        series_id: str | None = None,
        gate_offset: int = 0,
        gate_limit: int = 50,
        run_id: str | None = None,
    ) -> CampaignSnapshot:
        """Return current domain facts plus a graph-derived Dagster spine."""

        run_id = _optional(run_id)
        definition = self._campaign(campaign_id)
        projector, keys = self._services(definition)
        view = keys.run_view(run_id)
        state = ("historical", *view) if view else keys.campaign_state()
        key = ("campaign", campaign_id, definition.spec.revision, state,
               series_id, gate_offset, gate_limit)
        return self.cache.get_or_create(key, lambda: CampaignSnapshot(
            campaign=campaign_card(definition), generated_at=datetime.now(UTC),
            lens=projector.project(
                series_id=series_id, gate_offset=gate_offset, gate_limit=gate_limit,
                snapshot_id=view[0] if view else None,
                override_revision=view[1] if view else None,
                view_run_id=run_id,
            ),
        ))

    def get_stage_results(
        self,
        campaign_id: str,
        stage: str,
        *,
        series_id: str | None = None,
        offset: int = 0,
        limit: int = 50,
        run_id: str | None = None,
    ) -> StageResultPage:
        """Return one bounded stage-owned table only after explicit inspection."""

        run_id = _optional(run_id)
        definition = self._campaign(campaign_id)
        if stage not in {item.stage for item in definition.spine}:
            raise KeyError(stage)
        _, keys = self._services(definition)
        view = keys.run_view(run_id)
        head = ("historical", *view) if view else keys.product_head(stage)
        key = ("stage-page", stage, head, series_id, offset, limit)
        return self.cache.get_or_create(key, lambda: self._stage_results.page(
            stage, series_id=series_id, offset=offset, limit=limit,
            snapshot_id=view[0] if view else None,
        ))

    def get_candidates(
        self, campaign_id: str, locator: tuple[str, str, str], *,
        run_id: str | None = None,
    ):
        run_id = _optional(run_id)
        definition = self._campaign(campaign_id)
        projector, keys = self._services(definition)
        view = keys.run_view(run_id)
        token = ("historical", *view) if view else keys.domain_state()
        key = ("candidates", locator, token)
        return self.cache.get_or_create(key, lambda: projector.candidates(
            locator, snapshot_id=view[0] if view else None,
            override_revision=view[1] if view else None,
        ))

    def get_product_snapshot(
        self, campaign_id: str, *, series_id: str | None, offset: int,
        limit: int, run_id: str | None = None,
    ) -> CampaignSnapshot:
        """Page the product with no Dagster history query or spine reconstruction."""

        run_id = _optional(run_id)
        definition = self._campaign(campaign_id)
        projector, keys = self._services(definition)
        view = keys.run_view(run_id)
        token = ("historical", *view) if view else keys.domain_state()
        key = ("product-page", campaign_id, token, series_id, offset, limit)
        return self.cache.get_or_create(key, lambda: CampaignSnapshot(
            campaign=campaign_card(definition), generated_at=datetime.now(UTC),
            lens=projector.project_product(
                series_id=series_id, gate_offset=offset, gate_limit=limit,
                snapshot_id=view[0] if view else None,
                override_revision=view[1] if view else None, view_run_id=run_id,
            ),
        ))

    def list_runs(self, *, offset: int = 0, limit: int = 50) -> RunPage:
        _validate_page(offset, limit)
        definition = self.campaigns.all()[0]
        return RunHistoryReader(
            self.repository, self.gateway, job_name=definition.spec.job_name
        ).list(offset=offset, limit=limit)

    def get_run(self, run_id: str) -> RunSummary:
        definition = self.campaigns.all()[0]
        return RunHistoryReader(
            self.repository, self.gateway, job_name=definition.spec.job_name
        ).get(run_id)

    def _services(self, definition: CampaignDefinition):
        keys = CampaignKeys(
            self.repository, self.gateway, job_name=definition.spec.job_name
        )
        projector = CanonicalizationLensProjector(
            self.repository, self.gateway, spine=definition.spine,
            job_name=definition.spec.job_name,
        )
        return projector, keys

    def _campaign(self, campaign_id: str) -> CampaignDefinition:
        return self.campaigns.get(campaign_id)


def _validate_page(offset: int, limit: int) -> None:
    if offset < 0 or limit < 1 or limit > 500:
        raise ValueError("offset must be non-negative and limit must be 1..500")


def _optional(value: str | None) -> str | None:
    return value.strip() or None if value is not None else None
