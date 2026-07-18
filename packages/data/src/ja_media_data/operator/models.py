"""Typed JSON contracts for the first operator campaign."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, computed_field

from ja_media_data.operator.registry import RecipeSpec


class OperatorModel(BaseModel):
    """Strict base for stable application/API response contracts."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class CampaignCard(OperatorModel):
    """One campaign suitable for the workbench index."""

    campaign_id: str
    name: str
    target: str
    scope: str
    description: str


class StageObservation(OperatorModel):
    """Current durable output and latest-run state for one campaign stage."""

    stage: str
    label: str
    status: str
    input_rows: int
    output_rows: int
    fingerprint: str | None = None
    materialization_id: str | None = None
    snapshot_id: int | None = None
    materialized_at: datetime | None = None
    output_run_id: str | None = None
    output_run_number: int | None = None
    output_attempt_id: str | None = None
    currency: str = "missing"
    stale_reason: str | None = None
    latest_run_status: str | None = None
    latest_run_id: str | None = None
    latest_run_number: int | None = None
    latest_attempt_id: str | None = None
    latest_run_started_at: datetime | None = None
    latest_run_duration_ms: int | None = None
    latest_run_error: str | None = None


class CandidateObservation(OperatorModel):
    """One capture considered by the canonicalization gate."""

    capture_id: str
    proposal_id: str | None = None
    acceptance_id: str | None = None
    manifest_bucket: str
    manifest_key: str
    manifest_modified_at: datetime
    admitted: bool
    selected: bool
    source: str

    @computed_field
    @property
    def manifest_url(self) -> str:
        """Return the stable object identity without exposing S3 credentials."""

        return f"s3://{self.manifest_bucket}/{self.manifest_key}"


class CanonicalizationGate(OperatorModel):
    """Explain the effective decision for one episode locator."""

    locator: str
    namespace: str
    series_id: str
    episode: str
    status: str
    selected_capture_id: str | None = None
    selected_manifest_url: str | None = None
    binding_source: str | None = None
    canonical_attempt_id: str | None = None
    active_override: str | None = None
    selection_reason: str
    candidate_count: int
    admitted_count: int
    candidates: tuple[CandidateObservation, ...]

    @computed_field
    @property
    def series_url(self) -> str | None:
        """Return the authoritative external series page when one is known."""

        return _series_url(self.namespace, self.series_id)

class CampaignProgress(OperatorModel):
    """Small truthful rollup derived from gate rows, never stored separately."""

    captures: int
    proposals: int
    quarantined: int
    locators: int
    canonicalized: int
    stale: int = 0
    unbound: int
    awaiting_acceptance: int
    awaiting_canonicalization: int


class ResolutionIssueObservation(OperatorModel):
    """One Bronze input quarantined before a binding proposal could be made."""

    issue_id: str
    capture_id: str
    hint_id: str | None = None
    kind: str
    namespace: str
    series_id: str
    manifest_key: str
    details_json: str

    @computed_field
    @property
    def series_url(self) -> str | None:
        """Return the authoritative external series page when one is known."""

        return _series_url(self.namespace, self.series_id)


class CanonicalizationLens(OperatorModel):
    """Specialized evidence projection nested beneath a generic campaign shell."""

    kind: Literal["canonicalization"] = "canonicalization"
    policy_version: str
    progress: CampaignProgress
    stages: tuple[StageObservation, ...]
    gates: tuple[CanonicalizationGate, ...]
    gate_total: int
    gate_offset: int
    gate_limit: int
    issues: tuple[ResolutionIssueObservation, ...]
    product_materialization_id: str | None = None
    product_run_id: str | None = None
    product_run_number: int | None = None
    product_attempt_id: str | None = None
    product_currency: str = "missing"
    product_stale_reason: str | None = None
    view_snapshot_id: int | None = None
    view_run_id: str | None = None


class CampaignSnapshot(OperatorModel):
    """Generic campaign envelope whose lens may vary by target family."""

    campaign: CampaignCard
    generated_at: datetime
    lens: CanonicalizationLens


class AcceptanceObservation(OperatorModel):
    """One automatically admitted resolver proposal."""

    acceptance_id: str
    proposal_id: str
    namespace: str
    series_id: str
    episode: str
    capture_id: str
    method: str
    policy_version: str
    computed_at: datetime
    manifest_url: str

    @computed_field
    @property
    def series_url(self) -> str | None:
        return _series_url(self.namespace, self.series_id)


class CanonicalInputObservation(OperatorModel):
    """One selected episode input from the canonical product."""

    canonical_id: str
    namespace: str
    series_id: str
    episode: str
    capture_id: str
    binding_source: str
    manifest_url: str
    computed_at: datetime

    @computed_field
    @property
    def series_url(self) -> str | None:
        return _series_url(self.namespace, self.series_id)


StageResultItem = ResolutionIssueObservation | AcceptanceObservation | CanonicalInputObservation


class StageResultPage(OperatorModel):
    """One bounded, stage-specific result projection for lazy inspection."""

    stage: str
    label: str
    items: tuple[StageResultItem, ...]
    total: int
    offset: int
    limit: int


class RecipeObservation(OperatorModel):
    """Registered recipe with bounded observations from durable runs."""

    recipe: RecipeSpec
    run_count: int = 0
    failure_count: int = 0
    current_products: int = 0
    latest_run_at: datetime | None = None


class RecipePage(OperatorModel):
    """Stable paged recipe response for CLI and future adapters."""

    items: tuple[RecipeObservation, ...]
    total: int
    offset: int
    limit: int


class StageCheckpointSummary(OperatorModel):
    """One local stage checkpoint within a global pipeline run."""

    stage: str
    ordinal: int
    disposition: str
    attempt_id: str | None = None
    materialization_id: str | None = None
    recipe_revision: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None


class RunSummary(OperatorModel):
    """One global dispatch composed from independently atomic checkpoints."""

    run_id: str
    run_number: int
    target: str
    forced_from_stage: str | None = None
    machine: str
    status: str
    started_at: datetime
    finished_at: datetime | None = None
    terminal_snapshot_id: int | None = None
    override_revision: int = 0
    duration_ms: int | None = None
    error: str | None = None
    checkpoints: tuple[StageCheckpointSummary, ...] = ()


class RunPage(OperatorModel):
    """Paged run summaries ordered by latest activity."""

    items: tuple[RunSummary, ...]
    total: int
    offset: int
    limit: int


def _series_url(namespace: str, series_id: str) -> str | None:
    if namespace == "anilist" and series_id.isdecimal():
        return f"https://anilist.co/anime/{series_id}"
    return None
