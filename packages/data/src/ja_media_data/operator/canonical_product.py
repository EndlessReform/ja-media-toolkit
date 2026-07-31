"""Paged canonical product rows and separately loaded candidate evidence."""

from __future__ import annotations

from ja_media_data.lakehouse.repository import DuckLakeRepository
from ja_media_data.lakehouse.time_travel import table_ref
from ja_media_data.operator.models import (
    CampaignProgress,
    CandidateObservation,
    CanonicalizationGate,
)


class CanonicalProductProjector:
    """Project bounded product rows without reconstructing the campaign."""

    def __init__(self, repository: DuckLakeRepository) -> None:
        self.repository = repository

    def page(
        self,
        *,
        series_id: str | None,
        offset: int,
        limit: int,
        currency: str,
        stale_reason: str | None,
        snapshot_id: int | None,
        override_revision: int | None,
    ) -> tuple[tuple[CanonicalizationGate, ...], int]:
        """Load only locator keys and aggregates for the requested product page."""

        overrides = self._overrides(series_id, override_revision)
        keys = set(self._locator_keys(series_id, snapshot_id)) | set(overrides)
        ordered = sorted(keys, key=_locator_sort_key)
        page_keys = ordered[offset : offset + limit]
        counts = self._candidate_counts(page_keys, snapshot_id)
        canonical = self._canonical(page_keys, snapshot_id)
        gates = tuple(
            self._gate(
                key,
                canonical.get(key),
                overrides.get(key),
                counts.get(key, (0, 0)),
                currency,
                stale_reason,
            )
            for key in page_keys
        )
        return gates, len(ordered)

    def candidates(
        self,
        locator: tuple[str, str, str],
        *,
        snapshot_id: int | None,
        override_revision: int | None,
    ) -> tuple[CandidateObservation, ...]:
        """Load full evidence only after an operator expands one locator."""

        proposals = table_ref(
            "episode_binding_proposals", snapshot_id=snapshot_id, alias="proposal"
        )
        captures = table_ref(
            "bronze_captures", snapshot_id=snapshot_id, alias="capture"
        )
        accepted = table_ref(
            "accepted_bindings_auto", snapshot_id=snapshot_id, alias="accepted"
        )
        rows = self.repository.connection.execute(
            f"""SELECT proposal.proposal_id, proposal.audio_capture_id,
                       accepted.acceptance_id, capture.manifest_key,
                       capture.manifest_bucket,
                       coalesce(capture.manifest_modified_at, capture.last_observed_at)
                FROM {proposals}
                JOIN {captures} ON capture.capture_id = proposal.audio_capture_id
                LEFT JOIN {accepted} ON accepted.proposal_id = proposal.proposal_id
                WHERE proposal.namespace = ? AND proposal.series_id = ?
                  AND proposal.episode = ?
                ORDER BY capture.manifest_modified_at DESC,
                         proposal.audio_capture_id""",
            list(locator),
        ).fetchall()
        selected = self._canonical([locator], snapshot_id).get(locator)
        selected_capture = selected[0] if selected else None
        items = [
            CandidateObservation(
                proposal_id=str(row[0]),
                capture_id=str(row[1]),
                acceptance_id=str(row[2]) if row[2] else None,
                manifest_key=str(row[3]),
                manifest_bucket=str(row[4]),
                manifest_modified_at=row[5],
                admitted=row[2] is not None,
                selected=str(row[1]) == selected_capture,
                source="proposal",
            )
            for row in rows
        ]
        override = self._overrides(locator[1], override_revision).get(locator)
        visible = getattr(override, "audio_capture_id", None) or selected_capture
        if visible and all(item.capture_id != visible for item in items):
            candidate = self._capture_candidate(
                visible, selected=visible == selected_capture, snapshot_id=snapshot_id
            )
            if candidate:
                items.append(candidate)
        return tuple(items)

    def progress(
        self,
        *,
        series_id: str | None,
        total_locators: int,
        currency: str,
        snapshot_id: int | None,
        override_revision: int | None,
    ) -> CampaignProgress:
        """Compute campaign counters with scalar aggregates, never product rows."""

        captures = table_ref(
            "bronze_captures", snapshot_id=snapshot_id, alias="capture"
        )
        proposals = table_ref(
            "episode_binding_proposals", snapshot_id=snapshot_id, alias="proposal"
        )
        accepted = table_ref(
            "accepted_bindings_auto", snapshot_id=snapshot_id, alias="accepted"
        )
        canonical = table_ref(
            "canonical_episode_inputs", snapshot_id=snapshot_id, alias="canonical"
        )
        issues = table_ref(
            "resolution_issues_auto", snapshot_id=snapshot_id, alias="issue"
        )
        filters = " WHERE series_id = ?" if series_id else ""
        capture_filter = " WHERE capture.series_id = ?" if series_id else ""
        parameters = [series_id] if series_id else []
        row = self.repository.connection.execute(
            f"""SELECT
                (SELECT count(*) FROM {captures}{capture_filter}),
                (SELECT count(*) FROM {proposals}{filters}),
                (SELECT count(*) FROM {issues} JOIN {captures}
                   ON capture.capture_id = issue.capture_id{capture_filter}),
                (SELECT count(*) FROM (SELECT DISTINCT namespace, series_id, episode
                   FROM {accepted}{filters})),
                (SELECT count(*) FROM {canonical}{filters})""",
            parameters * 5,
        ).fetchone()
        canonical_count = int(row[4])
        overrides = self._overrides(series_id, override_revision)
        unbound = sum(
            getattr(item, "audio_capture_id", None) is None
            for item in overrides.values()
        )
        admitted_locators = int(row[3])
        stale = canonical_count if currency.startswith("stale") else 0
        return CampaignProgress(
            captures=int(row[0]),
            proposals=int(row[1]),
            quarantined=int(row[2]),
            locators=total_locators,
            canonicalized=0 if stale else canonical_count,
            stale=stale,
            unbound=unbound,
            awaiting_acceptance=max(0, total_locators - admitted_locators - unbound),
            awaiting_canonicalization=max(0, admitted_locators - canonical_count),
        )

    def _locator_keys(
        self, series_id: str | None, snapshot_id: int | None
    ) -> list[tuple[str, str, str]]:
        proposals = table_ref("episode_binding_proposals", snapshot_id=snapshot_id)
        canonical = table_ref("canonical_episode_inputs", snapshot_id=snapshot_id)
        where = " WHERE series_id = ?" if series_id else ""
        params = [series_id, series_id] if series_id else []
        rows = self.repository.connection.execute(
            f"""SELECT namespace, series_id, episode FROM {proposals}{where}
                UNION SELECT namespace, series_id, episode FROM {canonical}{where}""",
            params,
        ).fetchall()
        return [(str(row[0]), str(row[1]), str(row[2])) for row in rows]

    def _candidate_counts(
        self, keys: list[tuple[str, str, str]], snapshot_id: int | None
    ) -> dict[tuple[str, str, str], tuple[int, int]]:
        if not keys:
            return {}
        proposals = table_ref(
            "episode_binding_proposals", snapshot_id=snapshot_id, alias="proposal"
        )
        accepted = table_ref(
            "accepted_bindings_auto", snapshot_id=snapshot_id, alias="accepted"
        )
        where, params = _locator_predicate("proposal", keys)
        rows = self.repository.connection.execute(
            f"""SELECT proposal.namespace, proposal.series_id, proposal.episode,
                       count(*), count(accepted.acceptance_id)
                FROM {proposals}
                LEFT JOIN {accepted} ON accepted.proposal_id = proposal.proposal_id
                WHERE {where}
                GROUP BY proposal.namespace, proposal.series_id, proposal.episode""",
            params,
        ).fetchall()
        return {
            (str(row[0]), str(row[1]), str(row[2])): (int(row[3]), int(row[4]))
            for row in rows
        }

    def _canonical(
        self, keys: list[tuple[str, str, str]], snapshot_id: int | None
    ) -> dict[tuple[str, str, str], tuple[str, str, str | None, str]]:
        if not keys:
            return {}
        source = table_ref(
            "canonical_episode_inputs", snapshot_id=snapshot_id, alias="canonical"
        )
        where, params = _locator_predicate("canonical", keys)
        rows = self.repository.connection.execute(
            f"""SELECT namespace, series_id, episode, audio_capture_id,
                       binding_source, run_id, manifest_bucket, manifest_key
                FROM {source} WHERE {where}""",
            params,
        ).fetchall()
        return {
            (str(row[0]), str(row[1]), str(row[2])): (
                str(row[3]),
                str(row[4]),
                str(row[5]) if row[5] else None,
                f"s3://{row[6]}/{row[7]}",
            )
            for row in rows
        }

    def _overrides(
        self, series_id: str | None, revision: int | None
    ) -> dict[tuple[str, str, str], object]:
        source = self.repository.override_repository
        if source is None:
            return {}
        iterator = (
            source.iter_overrides_at_revision(revision)
            if revision is not None and hasattr(source, "iter_overrides_at_revision")
            else source.iter_current_overrides()
        )
        return {
            (item.namespace, item.series_id, item.episode): item
            for item in iterator
            if series_id is None or item.series_id == series_id
        }

    def _capture_candidate(
        self, capture_id: str, *, selected: bool, snapshot_id: int | None
    ) -> CandidateObservation | None:
        source = table_ref("bronze_captures", snapshot_id=snapshot_id)
        row = self.repository.connection.execute(
            """SELECT manifest_key, manifest_bucket,
                      coalesce(manifest_modified_at, last_observed_at) FROM """
            + source
            + " WHERE capture_id = ? LIMIT 1",
            [capture_id],
        ).fetchone()
        return (
            CandidateObservation(
                capture_id=capture_id,
                manifest_key=str(row[0]),
                manifest_bucket=str(row[1]),
                manifest_modified_at=row[2],
                admitted=True,
                selected=selected,
                source="override",
            )
            if row
            else None
        )

    @staticmethod
    def _gate(
        locator: tuple[str, str, str],
        canonical: tuple[str, str, str | None, str] | None,
        override: object | None,
        counts: tuple[int, int],
        currency: str,
        stale_reason: str | None,
    ) -> CanonicalizationGate:
        selected = canonical[0] if canonical else None
        override_capture = getattr(override, "audio_capture_id", None)
        if selected and currency.startswith("stale"):
            status, reason = (
                "stale",
                "Committed canonical output is stale: " + (stale_reason or currency),
            )
        elif override is not None and override_capture != selected:
            action = (
                "unbind" if override_capture is None else f"select {override_capture}"
            )
            status, reason = (
                "awaiting_canonicalization",
                (
                    f"Active override says to {action}; the product has not incorporated it."
                ),
            )
        elif selected:
            status, reason = (
                "canonicalized",
                (
                    "Latest admitted manifest_modified_at wins; manifest key and capture ID break ties."
                ),
            )
        elif counts[1]:
            status, reason = (
                "awaiting_canonicalization",
                "Admitted candidates await compilation.",
            )
        else:
            status, reason = (
                "awaiting_acceptance",
                "Resolver proposals await admission.",
            )
        return CanonicalizationGate(
            locator=":".join(locator),
            namespace=locator[0],
            series_id=locator[1],
            episode=locator[2],
            status=status,
            selected_capture_id=selected,
            selected_manifest_url=canonical[3] if canonical else None,
            binding_source=canonical[1] if canonical else None,
            canonical_attempt_id=canonical[2] if canonical else None,
            active_override=(
                "unbind"
                if override is not None and override_capture is None
                else override_capture
            ),
            selection_reason=reason,
            candidate_count=counts[0],
            admitted_count=counts[1],
            candidates=(),
        )


def _locator_predicate(
    alias: str, keys: list[tuple[str, str, str]]
) -> tuple[str, list[str]]:
    clause = " OR ".join(
        f"({alias}.namespace = ? AND {alias}.series_id = ? AND {alias}.episode = ?)"
        for _ in keys
    )
    return clause, [value for key in keys for value in key]


def _locator_sort_key(locator: tuple[str, str, str]) -> tuple[str, str, str]:
    return locator[0], locator[1], locator[2].zfill(12)
