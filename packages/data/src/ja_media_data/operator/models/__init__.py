"""Stable operator DTOs grouped by the surface they describe."""

from .campaigns import CampaignCard, CampaignSnapshot
from .products import (
    AcceptanceObservation,
    CampaignProgress,
    CandidateObservation,
    CanonicalInputObservation,
    CanonicalizationGate,
    CanonicalizationLens,
    ResolutionIssueObservation,
    StageObservation,
    StageResultPage,
)
from .runs import RunPage, RunSummary, StageCheckpointSummary

__all__ = [
    "AcceptanceObservation", "CampaignCard", "CampaignProgress",
    "CampaignSnapshot", "CandidateObservation", "CanonicalInputObservation",
    "CanonicalizationGate", "CanonicalizationLens", "ResolutionIssueObservation",
    "RunPage", "RunSummary", "StageCheckpointSummary", "StageObservation",
    "StageResultPage",
]
