"""Versioned, conservative conversion from capture evidence to episode claims."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from ja_media_core.bronze import BronzeCaptureManifest

from ja_media_data.products.episode_resolution.metadata import SeriesEpisodeMetadata
from ja_media_data.products.episode_resolution.models import (
    BindingProposal,
    HintClaim,
    ResolutionIssueClaim,
)
from ja_media_data.products.episode_resolution.evidence import collect_episode_evidence


RECIPE_VERSION = "episode-filename-v1"


@dataclass(frozen=True)
class EpisodeResolutionPlan:
    """Pure resolution output plus an explainable classification."""

    classification: str
    reason: str
    hints: tuple[HintClaim, ...]
    proposal: BindingProposal | None
    issue: ResolutionIssueClaim | None
    evidence: dict[str, Any]


def plan_episode_resolution(
    manifest: BronzeCaptureManifest,
    *,
    input_data_version: str,
    metadata: SeriesEpisodeMetadata | None,
    run_source: str | None = None,
) -> EpisodeResolutionPlan:
    """Require filename-signal agreement and AniList bounds before acceptance."""

    signals = collect_episode_evidence(manifest, metadata)
    ptn_episode = signals.ptn_episode
    explicit_episodes = signals.explicit_episodes
    matched_title = signals.matched_title
    evidence = signals.details

    if signals.ranges:
        return _issue_plan(
            manifest,
            input_data_version,
            evidence,
            reason="multi_episode_range",
            kind="ambiguous",
            run_source=run_source,
        )
    candidates = tuple(
        sorted(set(explicit_episodes) | ({ptn_episode} if ptn_episode else set()))
    )
    hints = _candidate_hints(
        manifest,
        candidates,
        ptn_episode=ptn_episode,
        explicit_episodes=explicit_episodes,
        evidence=evidence,
        input_data_version=input_data_version,
        run_source=run_source,
    )
    if not candidates:
        reason = (
            "non_episodic_format"
            if metadata and metadata.media_format == "MOVIE"
            else "no_ordinary_episode"
        )
        return _issue_plan(
            manifest,
            input_data_version,
            evidence,
            reason=reason,
            kind="ambiguous",
            run_source=run_source,
        )
    if len(candidates) != 1 or ptn_episode != candidates[0] or explicit_episodes != candidates:
        return _issue_plan(
            manifest,
            input_data_version,
            evidence,
            reason="parser_signals_disagree",
            kind="ambiguous",
            hints=hints,
            run_source=run_source,
        )

    episode = candidates[0]
    if metadata is None or not metadata.titles:
        return _issue_plan(
            manifest,
            input_data_version,
            evidence,
            reason="title_metadata_unavailable",
            kind="ambiguous",
            hints=hints,
            run_source=run_source,
        )
    if matched_title is None:
        return _issue_plan(
            manifest,
            input_data_version,
            evidence,
            reason="filename_title_disagrees_with_anilist",
            kind="invalid",
            hints=hints,
            run_source=run_source,
        )
    if metadata.episode_count is None:
        return _issue_plan(
            manifest,
            input_data_version,
            evidence,
            reason="episode_count_unavailable",
            kind="ambiguous",
            hints=hints,
            run_source=run_source,
        )
    if metadata.media_format == "MOVIE" or episode > metadata.episode_count:
        return _issue_plan(
            manifest,
            input_data_version,
            evidence,
            reason=(
                "non_episodic_format"
                if metadata.media_format == "MOVIE"
                else "episode_exceeds_anilist_count"
            ),
            kind="invalid",
            hints=hints,
            run_source=run_source,
        )

    hint = hints[0]
    proposal = BindingProposal(
        proposal_id=_stable_id("proposal", hint.hint_id),
        namespace=manifest.series.namespace,
        series_id=manifest.series.identifier,
        episode=str(episode),
        audio_capture_id=manifest.capture_id,
        proposal_method="automatic-filename-and-anilist-bounds",
        proposal_evidence={"hint_id": hint.hint_id, **evidence},
        input_data_version=input_data_version,
        recipe_version=RECIPE_VERSION,
        run_source=run_source,
    )
    return EpisodeResolutionPlan(
        classification="proposed",
        reason="signals_agree_and_episode_is_in_bounds",
        hints=hints,
        proposal=proposal,
        issue=None,
        evidence=evidence,
    )


def _candidate_hints(
    manifest: BronzeCaptureManifest,
    candidates: tuple[int, ...],
    *,
    ptn_episode: int | None,
    explicit_episodes: tuple[int, ...],
    evidence: dict[str, Any],
    input_data_version: str,
    run_source: str | None,
) -> tuple[HintClaim, ...]:
    results = []
    for candidate in candidates:
        ptn_supports = ptn_episode == candidate
        token_supports = candidate in explicit_episodes
        method = (
            "ptn+explicit-episode-token"
            if ptn_supports and token_supports
            else "ptn"
            if ptn_supports
            else "explicit-episode-token"
        )
        confidence = 0.98 if ptn_supports and token_supports else 0.7
        results.append(
            HintClaim(
                hint_id=_stable_id(
                    "hint",
                    manifest.capture_id,
                    input_data_version,
                    RECIPE_VERSION,
                    method,
                    str(candidate),
                ),
                capture_id=manifest.capture_id,
                candidate_namespace=manifest.series.namespace,
                candidate_series_id=manifest.series.identifier,
                candidate_episode=str(candidate),
                method=method,
                confidence=confidence,
                evidence=evidence,
                input_data_version=input_data_version,
                recipe_version=RECIPE_VERSION,
                run_source=run_source,
            )
        )
    return tuple(results)


def _issue_plan(
    manifest: BronzeCaptureManifest,
    input_data_version: str,
    evidence: dict[str, Any],
    *,
    reason: str,
    kind: str,
    hints: tuple[HintClaim, ...] = (),
    run_source: str | None = None,
) -> EpisodeResolutionPlan:
    issue = ResolutionIssueClaim(
        issue_id=_stable_id(
            "issue", manifest.capture_id, input_data_version, RECIPE_VERSION, reason
        ),
        capture_id=manifest.capture_id,
        hint_id=hints[0].hint_id if hints else None,
        kind=kind,
        details={"reason": reason, "recipe_version": RECIPE_VERSION, **evidence},
        run_source=run_source,
    )
    return EpisodeResolutionPlan(
        classification="quarantined",
        reason=reason,
        hints=hints,
        proposal=None,
        issue=issue,
        evidence=evidence,
    )


def overlap_issue(
    plan: EpisodeResolutionPlan, *, capture_id: str, input_data_version: str
) -> ResolutionIssueClaim:
    """Convert a transactional uniqueness conflict into a durable review item."""

    assert plan.proposal is not None
    return ResolutionIssueClaim(
        issue_id=_stable_id(
            "issue", capture_id, input_data_version, RECIPE_VERSION, "overlap"
        ),
        capture_id=capture_id,
        hint_id=plan.hints[0].hint_id if plan.hints else None,
        kind="overlap",
        details={
            "reason": "locator_or_capture_already_bound",
            "candidate_locator": {
                "namespace": plan.proposal.namespace,
                "series_id": plan.proposal.series_id,
                "episode": plan.proposal.episode,
            },
            "recipe_version": RECIPE_VERSION,
            **plan.evidence,
        },
        run_source=plan.proposal.run_source,
    )


def invalid_manifest_issue(
    *,
    capture_id: str,
    input_data_version: str,
    error: str,
    manifest_key: str,
    run_source: str | None = None,
) -> ResolutionIssueClaim:
    """Create a deterministic DLQ row for an unreadable committed manifest."""

    return ResolutionIssueClaim(
        issue_id=_stable_id(
            "issue", capture_id, input_data_version, RECIPE_VERSION, "invalid_manifest"
        ),
        capture_id=capture_id,
        hint_id=None,
        kind="invalid",
        details={
            "reason": "invalid_manifest",
            "error": error,
            "manifest_key": manifest_key,
            "recipe_version": RECIPE_VERSION,
        },
        run_source=run_source,
    )


def _stable_id(prefix: str, *parts: str) -> str:
    """Derive a deterministic display/idempotency key from immutable evidence."""
    encoded = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
    return f"{prefix}-{hashlib.sha256(encoded.encode()).hexdigest()[:40]}"
