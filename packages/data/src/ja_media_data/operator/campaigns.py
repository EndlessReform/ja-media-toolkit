"""Checked-in campaign definitions for the operator application."""

from __future__ import annotations

from dataclasses import dataclass

from ja_media_data.operator.models import CampaignCard
from ja_media_data.operator.phase_d_registry import CORPUS
from ja_media_data.operator.registry import CampaignSpec, ScopeSpec



@dataclass(frozen=True)
class CampaignDefinition:
    """Saved intent plus the presentation lens appropriate to its target."""

    spec: CampaignSpec
    lens_kind: str


CANONICALIZATION_CAMPAIGN = CampaignDefinition(
    spec=CampaignSpec(
        campaign_id="canonicalization-gate",
        label="Binding & canonicalization desk",
        note=(
            "Inspect resolver binding proposals, automatic admission, active "
            "overrides, and the resulting canonical capture in one desk."
        ),
        target="canonical-inputs",
        scope=ScopeSpec(selector="corpus", members=(CORPUS,)),
    ),
    lens_kind="canonicalization",
)

CAMPAIGNS = {
    CANONICALIZATION_CAMPAIGN.spec.campaign_id: CANONICALIZATION_CAMPAIGN,
}


def campaign_card(spec: CampaignSpec) -> CampaignCard:
    """Project saved intent into the compact workboard contract."""

    return CampaignCard(
        campaign_id=spec.campaign_id,
        name=spec.label,
        target=spec.target,
        scope=spec.scope.selector,
        description=spec.note,
    )
