"""Bounded domain-product projections for the canonicalization workbench."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import computed_field

from ja_media_data.operator.models.base import OperatorModel


class StageObservation(OperatorModel):
    """Domain output head joined to the latest Dagster step execution."""

    stage: str
    step_key: str
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
    latest_run_started_at: datetime | None = None
    latest_run_duration_ms: int | None = None
    latest_run_error: str | None = None
    dagster_url: str | None = None


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
        return _series_url(self.namespace, self.series_id)


class CampaignProgress(OperatorModel):
    """Small rollup derived from product rows, never Dagster events."""

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
        return _series_url(self.namespace, self.series_id)


class AcceptanceObservation(OperatorModel):
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
    stage: str
    label: str
    items: tuple[StageResultItem, ...]
    total: int
    offset: int
    limit: int


class CanonicalizationLens(OperatorModel):
    """Canonical domain facts nested beneath a graph-derived campaign shell."""

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


def _series_url(namespace: str, series_id: str) -> str | None:
    if namespace == "anilist" and series_id.isdecimal():
        return f"https://anilist.co/anime/{series_id}"
    return None
