"""Checked-in campaign presets validated against the Dagster asset graph."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tomllib

import dagster as dg
from pydantic import BaseModel, ConfigDict, Field

from ja_media_data.operator.models import CampaignCard
from ja_media_data.orchestration.dagster.presentation import BY_OP, StagePresentation


_PACKAGED_DIR = Path(__file__).parents[1] / "campaigns"
CAMPAIGN_DIR = (
    _PACKAGED_DIR if _PACKAGED_DIR.is_dir() else Path(__file__).parents[3] / "campaigns"
)


class CampaignSpec(BaseModel):
    """Revisioned operator preset over invariant Dagster asset definitions."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(ge=1, le=1)
    campaign_id: str = Field(pattern=r"^[a-z][a-z0-9-]+$")
    revision: int = Field(ge=1)
    label: str
    description: str
    job_name: str
    target_assets: tuple[str, ...] = Field(min_length=1)
    scope_kind: str
    lens_kind: str


@dataclass(frozen=True)
class CampaignDefinition:
    """Validated preset plus its graph-derived ordered computation spine."""

    spec: CampaignSpec
    spine: tuple[StagePresentation, ...]


class CampaignCatalog:
    """Load TOML presets and reject references absent from loaded definitions."""

    def __init__(self, definitions: dg.Definitions, directory: Path = CAMPAIGN_DIR) -> None:
        self.definitions = definitions
        self._campaigns = self._load(directory)

    def all(self) -> tuple[CampaignDefinition, ...]:
        return tuple(self._campaigns[key] for key in sorted(self._campaigns))

    def get(self, campaign_id: str) -> CampaignDefinition:
        try:
            return self._campaigns[campaign_id]
        except KeyError as error:
            raise KeyError(campaign_id) from error

    def _load(self, directory: Path) -> dict[str, CampaignDefinition]:
        campaigns: dict[str, CampaignDefinition] = {}
        for path in sorted(directory.glob("*.toml")):
            spec = CampaignSpec.model_validate(tomllib.loads(path.read_text()))
            if spec.campaign_id in campaigns:
                raise ValueError(f"duplicate campaign ID: {spec.campaign_id}")
            campaigns[spec.campaign_id] = CampaignDefinition(
                spec=spec, spine=self._resolve_spine(spec)
            )
        if not campaigns:
            raise ValueError(f"no campaign TOML files found in {directory}")
        return campaigns

    def _resolve_spine(self, spec: CampaignSpec) -> tuple[StagePresentation, ...]:
        graph = self.definitions.resolve_asset_graph()
        available = {key.to_user_string(): key for key in graph.get_all_asset_keys()}
        missing = sorted(set(spec.target_assets) - available.keys())
        if missing:
            raise ValueError(
                f"campaign {spec.campaign_id} references missing assets: {missing}"
            )
        if spec.job_name not in {
            job.name for job in self.definitions.resolve_all_job_defs()
        }:
            raise ValueError(
                f"campaign {spec.campaign_id} references missing job: {spec.job_name}"
            )
        targets = {available[name] for name in spec.target_assets}
        closure = set(targets)
        for target in targets:
            closure.update(graph.get_ancestor_asset_keys(target))
        ordered: list[StagePresentation] = []
        seen: set[str] = set()
        for level in graph.toposorted_asset_keys_by_level:
            for key in sorted(level, key=lambda item: item.to_user_string()):
                if key not in closure:
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
