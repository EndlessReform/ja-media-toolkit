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
from ja_media_data.products.episode_resolution.reasons import (
    BRONZE_MANIFEST_FAILED_SCHEMA_VALIDATION,
    DECLARED_ANILIST_ENTRY_HAS_NO_EPISODE_COUNT,
    DECLARED_ANILIST_ENTRY_HAS_NO_TITLES,
    DECLARED_ANILIST_ENTRY_IS_MOVIE,
    DECLARED_ANILIST_ID_NOT_FOUND_IN_METADATA,
    FILENAME_AND_DECLARED_ANILIST_ENTRY_AGREE,
    FILENAME_CONTAINS_MULTI_EPISODE_RANGE,
    FILENAME_EPISODE_EXCEEDS_DECLARED_ANILIST_COUNT,
    FILENAME_TITLE_NOT_EQUAL_TO_DECLARED_ANILIST_TITLES,
    episode_signal_failure_reason,
)


RECIPE_VERSION = "episode-filename-v2"


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
            reason=FILENAME_CONTAINS_MULTI_EPISODE_RANGE,
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
    signal_failure = episode_signal_failure_reason(ptn_episode, explicit_episodes)
    if signal_failure is not None:
        return _issue_plan(
            manifest,
            input_data_version,
            evidence,
            reason=(
                DECLARED_ANILIST_ENTRY_IS_MOVIE
                if metadata and metadata.media_format == "MOVIE" and not candidates
                else signal_failure
            ),
            kind="ambiguous",
            hints=hints,
            run_source=run_source,
        )

    assert ptn_episode is not None
    episode = candidates[0]
    if metadata is None:
        return _issue_plan(
            manifest,
            input_data_version,
            evidence,
            reason=DECLARED_ANILIST_ID_NOT_FOUND_IN_METADATA,
            kind="ambiguous",
            hints=hints,
            run_source=run_source,
        )
    if not metadata.titles:
        return _issue_plan(
            manifest,
            input_data_version,
            evidence,
            reason=DECLARED_ANILIST_ENTRY_HAS_NO_TITLES,
            kind="ambiguous",
            hints=hints,
            run_source=run_source,
        )
    if matched_title is None:
        return _issue_plan(
            manifest,
            input_data_version,
            evidence,
            reason=FILENAME_TITLE_NOT_EQUAL_TO_DECLARED_ANILIST_TITLES,
            kind="invalid",
            hints=hints,
            run_source=run_source,
        )
    if metadata.episode_count is None:
        return _issue_plan(
            manifest,
            input_data_version,
            evidence,
            reason=DECLARED_ANILIST_ENTRY_HAS_NO_EPISODE_COUNT,
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
                DECLARED_ANILIST_ENTRY_IS_MOVIE
                if metadata.media_format == "MOVIE"
                else FILENAME_EPISODE_EXCEEDS_DECLARED_ANILIST_COUNT
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
        reason=FILENAME_AND_DECLARED_ANILIST_ENTRY_AGREE,
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


def manifest_schema_validation_issue(
    *,
    capture_id: str,
    input_data_version: str,
    error: str,
    manifest_key: str,
    run_source: str | None = None,
) -> ResolutionIssueClaim:
    """Create a deterministic review row for a manifest that failed validation."""

    return ResolutionIssueClaim(
        issue_id=_stable_id(
            "issue",
            capture_id,
            input_data_version,
            RECIPE_VERSION,
            BRONZE_MANIFEST_FAILED_SCHEMA_VALIDATION,
        ),
        capture_id=capture_id,
        hint_id=None,
        kind="invalid",
        details={
            "reason": BRONZE_MANIFEST_FAILED_SCHEMA_VALIDATION,
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
