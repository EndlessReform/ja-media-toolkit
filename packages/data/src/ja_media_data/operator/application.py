"""Surface-neutral use cases for the first real operator campaign."""

from __future__ import annotations

from datetime import UTC, datetime

from ja_media_data.lakehouse.repository import DuckLakeRepository
from ja_media_data.operator.campaigns import (
    CAMPAIGNS,
    CampaignDefinition,
    campaign_card,
)
from ja_media_data.operator.canonicalization import CanonicalizationLensProjector
from ja_media_data.operator.cache import ProjectionCache
from ja_media_data.operator.models import (
    CampaignCard,
    CampaignSnapshot,
    RecipeObservation,
    RecipePage,
    RunPage,
    RunSummary,
    StageResultPage,
)
from ja_media_data.operator.phase_d_registry import build_phase_d_registry
from ja_media_data.operator.planning import (
    CapabilityProfile,
    ExecutionIntent,
    ExecutionPlan,
    Planner,
)
from ja_media_data.operator.status import PhaseDStatusAdapter
from ja_media_data.operator.stage_results import StageResultProjector
from ja_media_data.operator.run_history import RunHistoryReader
from ja_media_data.operator.cache_keys import CampaignKeys


class OperatorApplication:
    """Own validation and snapshot composition independently of any UI framework."""

    def __init__(
        self, repository: DuckLakeRepository, *, cache: ProjectionCache | None = None
    ) -> None:
        self.repository = repository
        # ProjectionCache exposes __len__, so an empty shared cache is falsey.
        # Identity, not truthiness, decides whether the FastAPI-lifetime cache
        # was supplied.
        self.cache = cache if cache is not None else ProjectionCache()
        self.registry = build_phase_d_registry()
        self._canonicalization = CanonicalizationLensProjector(repository)
        self._stage_results = StageResultProjector(repository)
        self._history = RunHistoryReader(repository)
        self._keys = CampaignKeys(repository)
        self._lens_projectors = {
            "canonicalization": self._canonicalization.project,
        }

    def list_campaigns(self) -> tuple[CampaignCard, ...]:
        """Return checked-in campaigns without opening UI-specific state."""

        return tuple(
            campaign_card(definition.spec) for definition in CAMPAIGNS.values()
        )

    def get_campaign_snapshot(
        self,
        campaign_id: str,
        *,
        series_id: str | None = None,
        gate_offset: int = 0,
        gate_limit: int = 50,
        run_id: str | None = None,
    ) -> CampaignSnapshot:
        """Return the real Phase D target lens for one campaign."""

        run_id = _optional(run_id)
        definition = self._campaign(campaign_id)
        projector = self._lens_projectors[definition.lens_kind]
        view = self._keys.run_view(run_id)
        state_token = (
            ("historical", *view) if view else self._keys.campaign_state()
        )
        key = (
            "campaign", campaign_id, state_token, series_id,
            gate_offset, gate_limit,
        )
        return self.cache.get_or_create(key, lambda: CampaignSnapshot(
            campaign=campaign_card(definition.spec), generated_at=datetime.now(UTC),
            lens=projector(series_id=series_id, gate_offset=gate_offset,
                           gate_limit=gate_limit,
                           snapshot_id=view[0] if view else None,
                           override_revision=view[1] if view else None,
                           view_run_id=run_id),
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
        """Return a bounded stage-owned view only after an explicit inspection."""

        run_id = _optional(run_id)
        self._campaign(campaign_id)
        view = self._keys.run_view(run_id)
        head_token = (
            ("historical", *view) if view else self._keys.product_head(stage)
        )
        key = (
            "stage-page", stage, head_token, series_id,
            offset, limit,
        )
        return self.cache.get_or_create(key, lambda: self._stage_results.page(
            stage, series_id=series_id, offset=offset, limit=limit,
            snapshot_id=view[0] if view else None,
        ))

    def get_candidates(
        self, campaign_id: str, locator: tuple[str, str, str], *,
        run_id: str | None = None,
    ):
        """Return one lazily loaded candidate set from current or historical state."""

        run_id = _optional(run_id)
        self._campaign(campaign_id)
        view = self._keys.run_view(run_id)
        token = (("historical", *view) if view else self._keys.campaign_state())
        key = ("candidates", locator, token)
        return self.cache.get_or_create(key, lambda: self._canonicalization.candidates(
            locator, snapshot_id=view[0] if view else None,
            override_revision=view[1] if view else None,
        ))

    def get_product_snapshot(
        self, campaign_id: str, *, series_id: str | None, offset: int,
        limit: int, run_id: str | None = None,
    ) -> CampaignSnapshot:
        """Return one product page without rebuilding stage cards or counters."""

        run_id = _optional(run_id)
        definition = self._campaign(campaign_id)
        view = self._keys.run_view(run_id)
        token = (
            ("historical", *view) if view
            else self._keys.campaign_state()
        )
        key = ("product-page", campaign_id, token, series_id, offset, limit)
        return self.cache.get_or_create(key, lambda: CampaignSnapshot(
            campaign=campaign_card(definition.spec), generated_at=datetime.now(UTC),
            lens=self._canonicalization.project_product(
                series_id=series_id, gate_offset=offset, gate_limit=limit,
                snapshot_id=view[0] if view else None,
                override_revision=view[1] if view else None,
                view_run_id=run_id,
            ),
        ))

    def plan_campaign(
        self,
        campaign_id: str,
        *,
        capabilities: CapabilityProfile | None = None,
    ) -> ExecutionPlan:
        """Recompute a campaign plan from current durable products."""

        campaign = self._campaign(campaign_id).spec
        intent = ExecutionIntent(
            target=campaign.target,
            scope=campaign.scope,
            recipe_bindings=campaign.recipe_bindings,
            stop_target=campaign.stop_target,
        )
        status = PhaseDStatusAdapter(self.repository)
        return Planner(self.registry).plan(
            intent, status.observe, capabilities=capabilities
        )

    def list_recipes(
        self, *, query: str | None = None, offset: int = 0, limit: int = 50
    ) -> RecipePage:
        """Search registered recipes and attach run/product observations in bulk."""

        _validate_page(offset, limit)
        recipes = sorted(self.registry.recipes.values(), key=lambda item: item.recipe_id)
        if query:
            needle = query.casefold()
            recipes = [
                item for item in recipes
                if needle in " ".join((item.recipe_id, item.name, item.stage)).casefold()
            ]
        observations = self._history.recipe_observations()
        items = tuple(
            RecipeObservation(recipe=item, **observations.get(item.revision, {}))
            for item in recipes[offset : offset + limit]
        )
        return RecipePage(items=items, total=len(recipes), offset=offset, limit=limit)

    def list_runs(self, *, offset: int = 0, limit: int = 50) -> RunPage:
        """Return bounded global runs with their local stage checkpoints."""

        _validate_page(offset, limit)
        return self._history.list(offset=offset, limit=limit)

    def get_run(self, run_id: str) -> RunSummary:
        """Return one run detail or fail without leaking a storage row."""

        return self._history.get(run_id)

    @staticmethod
    def _campaign(campaign_id: str) -> CampaignDefinition:
        try:
            return CAMPAIGNS[campaign_id]
        except KeyError as error:
            raise KeyError(campaign_id) from error

def _validate_page(offset: int, limit: int) -> None:
    if offset < 0 or limit < 1 or limit > 500:
        raise ValueError("offset must be non-negative and limit must be 1..500")


def _optional(value: str | None) -> str | None:
    """Normalize blank query fields emitted by HTMX form inclusion."""

    return value.strip() or None if value is not None else None
