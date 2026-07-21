"""Operator projections of structurally registered Dagster campaigns."""

from __future__ import annotations

from dataclasses import dataclass

import dagster as dg

from ja_media_data.campaigns import CAMPAIGNS, OperatorCampaign
from ja_media_data.operator.models import CampaignCard
from ja_media_data.orchestration.dagster.presentation import BY_OP, StagePresentation


@dataclass(frozen=True)
class CampaignSpec:
    """Read model derived from one registered :class:`OperatorCampaign`."""

    campaign_id: str
    revision: int
    label: str
    description: str
    job_name: str
    target_assets: tuple[str, ...]
    scope_kind: str
    lens_kind: str

    @classmethod
    def from_campaign(cls, campaign: OperatorCampaign) -> CampaignSpec:
        presentation = campaign.presentation
        return cls(
            campaign_id=campaign.campaign_id,
            revision=campaign.revision,
            label=presentation.label,
            description=presentation.description,
            job_name=campaign.job.name,
            target_assets=presentation.conclusion_assets,
            scope_kind=presentation.scope_kind,
            lens_kind=presentation.lens_kind,
        )


@dataclass(frozen=True)
class CampaignDefinition:
    """Validated preset plus its graph-derived ordered computation spine."""

    spec: CampaignSpec
    spine: tuple[StagePresentation, ...]


class CampaignCatalog:
    """Validate registered campaign jobs and expose their operator projections."""

    def __init__(
        self,
        definitions: dg.Definitions,
        campaigns: tuple[OperatorCampaign, ...] = CAMPAIGNS,
    ) -> None:
        self.definitions = definitions
        self._campaigns = self._load(campaigns)

    def all(self) -> tuple[CampaignDefinition, ...]:
        return tuple(self._campaigns[key] for key in sorted(self._campaigns))

    def get(self, campaign_id: str) -> CampaignDefinition:
        try:
            return self._campaigns[campaign_id]
        except KeyError as error:
            raise KeyError(campaign_id) from error

    def _load(
        self, registered: tuple[OperatorCampaign, ...]
    ) -> dict[str, CampaignDefinition]:
        campaigns: dict[str, CampaignDefinition] = {}
        for campaign in registered:
            spec = CampaignSpec.from_campaign(campaign)
            if spec.campaign_id in campaigns:
                raise ValueError(f"duplicate campaign ID: {spec.campaign_id}")
            campaigns[spec.campaign_id] = CampaignDefinition(
                spec=spec, spine=self._resolve_spine(spec)
            )
        if not campaigns:
            raise ValueError("no operator campaigns registered")
        return campaigns

    def _resolve_spine(self, spec: CampaignSpec) -> tuple[StagePresentation, ...]:
        jobs = {job.name: job for job in self.definitions.resolve_all_job_defs()}
        if spec.job_name not in jobs:
            raise ValueError(
                f"campaign {spec.campaign_id} references missing job: {spec.job_name}"
            )
        job = jobs[spec.job_name]
        selected = job.asset_layer.selected_asset_keys
        selected_names = {key.to_user_string() for key in selected}
        missing = sorted(set(spec.target_assets) - selected_names)
        if missing:
            raise ValueError(
                f"campaign {spec.campaign_id} lens requires unselected assets: {missing}"
            )
        expected_tags = {
            "ja_media/campaign": spec.campaign_id,
            "ja_media/campaign_revision": str(spec.revision),
            "ja_media/scope": spec.scope_kind,
        }
        mismatched = {
            key: (job.tags.get(key), value)
            for key, value in expected_tags.items()
            if job.tags.get(key) != value
        }
        if mismatched:
            raise ValueError(
                f"campaign {spec.campaign_id} job tags diverged: {mismatched}"
            )
        graph = self.definitions.resolve_asset_graph()
        ordered: list[StagePresentation] = []
        seen: set[str] = set()
        for level in graph.toposorted_asset_keys_by_level:
            for key in sorted(level, key=lambda item: item.to_user_string()):
                if key not in selected:
                    continue
                node = graph.get(key)
                assets_def = node.assets_def
                op = getattr(assets_def, "op", None)
                presentation = BY_OP.get(op.name if op else "")
                if presentation and presentation.stage not in seen:
                    ordered.append(presentation)
                    seen.add(presentation.stage)
        if not ordered:
            raise ValueError(f"campaign {spec.campaign_id} has no presented stages")
        return tuple(ordered)


def campaign_card(definition: CampaignDefinition) -> CampaignCard:
    """Project a validated preset into the compact workboard contract."""

    spec = definition.spec
    return CampaignCard(
        campaign_id=spec.campaign_id,
        name=spec.label,
        target=", ".join(spec.target_assets),
        scope=spec.scope_kind,
        description=spec.description,
        revision=spec.revision,
    )
