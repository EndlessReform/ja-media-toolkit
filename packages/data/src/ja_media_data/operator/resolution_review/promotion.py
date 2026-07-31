"""Atomic promotion, history, and reversal for approved resolution drafts."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from psycopg import sql

from ja_media_data.lakehouse.repository import DuckLakeRepository
from ja_media_data.operator.resolution_review.drafts import (
    SourceTokenLike,
    flatten_draft,
)
from ja_media_data.operator.resolution_review.control_writer import (
    ResolutionControlWriter,
)
from ja_media_data.operator.resolution_review.factory import source_token
from ja_media_data.operator.resolution_review.history import ResolutionDecisionHistory
from ja_media_data.operator.resolution_review.models import SeriesResolutionDraft


class ResolutionPromotionService:
    """Promote a validated draft into PostgreSQL control heads as one transaction."""

    def __init__(self, repository: DuckLakeRepository, *, environment: str) -> None:
        if repository.override_repository is None:
            raise RuntimeError("PostgreSQL control storage is not configured")
        self.repository = repository
        self.overrides = repository.override_repository
        self.connection = self.overrides.connection
        self.schema = self.overrides.schema
        self.environment = environment
        self.writer = ResolutionControlWriter(repository, environment=environment)

    def accept(
        self, draft: SeriesResolutionDraft, reviewed: SourceTokenLike
    ) -> dict[str, object]:
        """Apply every reviewed decision or apply nothing if the review is stale."""

        current = source_token(self.repository)
        if (
            current.resolution_materialization_id,
            current.snapshot_id,
            current.binding_revision,
            current.disposition_revision,
        ) != (
            reviewed.resolution_materialization_id,
            reviewed.snapshot_id,
            reviewed.binding_revision,
            reviewed.disposition_revision,
        ):
            raise RuntimeError(
                "review inputs changed; run the review again before accepting"
            )
        rows = flatten_draft(draft)
        self._validate_current(rows)
        batch_id = f"resolution-batch-{uuid.uuid4().hex}"
        with self.connection.transaction():
            self.writer.lock()
            binding_before = self.writer.revision("binding_overrides")
            disposition_before = self.writer.revision("capture_dispositions")
            if binding_before != reviewed.binding_revision:
                raise RuntimeError("binding decisions changed; run the review again")
            if disposition_before != reviewed.disposition_revision:
                raise RuntimeError("capture dispositions changed; run the review again")
            binding_after = (
                self.writer.advance("binding_overrides")
                if any(row["destination_anilist_id"] is not None for row in rows)
                else binding_before
            )
            disposition_after = (
                self.writer.advance("capture_dispositions")
                if any(row["destination_anilist_id"] is None for row in rows)
                else disposition_before
            )
            self.writer.insert_batch(
                batch_id=batch_id,
                action="accept",
                reverses=None,
                draft=draft,
                reviewed=reviewed,
                reason=None,
                revisions=(
                    binding_before,
                    binding_after,
                    disposition_before,
                    disposition_after,
                ),
            )
            for index, row in enumerate(rows):
                self.writer.apply_row(
                    batch_id, index, row, binding_after, disposition_after
                )
        return {
            "status": "accepted",
            "batch_id": batch_id,
            "decisions": len(rows),
            "binding_revision": binding_after,
            "disposition_revision": disposition_after,
            "durable_writeback": True,
        }

    def reverse(self, batch_id: str, *, reason: str) -> dict[str, object]:
        """Restore the prior heads recorded by one accepted batch, failing stale."""

        reason = reason.strip()
        if not reason:
            raise ValueError("a reversal reason is required")
        with self.connection.transaction():
            self.writer.lock()
            batch = self._batch_row(batch_id)
            if batch is None or batch[1] != "accept":
                raise LookupError("accepted resolution batch was not found")
            if self._reversal_for(batch_id) is not None:
                raise RuntimeError("this resolution batch was already reversed")
            items = self._item_rows(batch_id)
            self._validate_reversal_heads(items)
            binding_before = self.writer.revision("binding_overrides")
            disposition_before = self.writer.revision("capture_dispositions")
            binding_after = (
                self.writer.advance("binding_overrides")
                if any(item[3] is not None for item in items)
                else binding_before
            )
            disposition_after = (
                self.writer.advance("capture_dispositions")
                if any(item[3] is None for item in items)
                else disposition_before
            )
            reverse_id = f"resolution-batch-{uuid.uuid4().hex}"
            reviewed = _StoredToken(batch[7], batch[8], batch[9], batch[10])
            draft = _StoredDraft(batch[4], f"Reversed {batch_id}: {reason}")
            self.writer.insert_batch(
                batch_id=reverse_id,
                action="reverse",
                reverses=batch_id,
                draft=draft,
                reviewed=reviewed,
                reason=reason,
                revisions=(
                    binding_before,
                    binding_after,
                    disposition_before,
                    disposition_after,
                ),
            )
            for item in items:
                self.writer.reverse_item(item, binding_after, disposition_after)
        return {
            "status": "reversed",
            "batch_id": reverse_id,
            "reversed_batch_id": batch_id,
            "decisions": len(items),
            "binding_revision": binding_after,
            "disposition_revision": disposition_after,
        }

    def history(self, *, limit: int = 50):
        """Return recent batches with reversal state and bounded item counts."""

        return ResolutionDecisionHistory(self.repository).recent(limit=limit)

    def disposed_capture_ids(self) -> set[str]:
        """Return active capture exclusions for immediate operator filtering."""

        return ResolutionDecisionHistory(self.repository).disposed_capture_ids()

    def _validate_current(self, rows: list[dict]) -> None:
        for row in rows:
            capture_id = row["capture_id"]
            if self.repository.get_capture(capture_id) is None:
                raise RuntimeError(f"capture disappeared since review: {capture_id}")
            if self.repository.get_current_binding_for_capture(capture_id) is not None:
                raise RuntimeError(f"capture was already resolved: {capture_id}")
            destination = row["destination_anilist_id"]
            if destination is None:
                continue
            existing = self.repository.get_current_binding(
                "anilist", str(destination), row["destination_episode"]
            )
            if existing is not None and existing.audio_capture_id != capture_id:
                raise RuntimeError(
                    f"anilist:{destination}:{row['destination_episode']} was already resolved"
                )

    def _batch_row(self, batch_id: str):
        return self.connection.execute(
            sql.SQL(
                "SELECT batch_id, action, reverses_batch_id, environment, current_anilist_id, "
                "summary, reason, resolution_materialization_id, snapshot_id, "
                "base_binding_revision, base_disposition_revision "
                "FROM {}.resolution_decision_batches WHERE batch_id = %s"
            ).format(self.schema),
            (batch_id,),
        ).fetchone()

    def _item_rows(self, batch_id: str):
        return self.connection.execute(
            sql.SQL(
                """SELECT item_index, capture_id, decision, destination_anilist_id,
                          destination_episode, rationale, applied_control_id,
                          prior_capture_id, prior_method, prior_note,
                          prior_disposition, prior_disposition_note
                     FROM {}.resolution_decision_items WHERE batch_id = %s
                     ORDER BY item_index"""
            ).format(self.schema),
            (batch_id,),
        ).fetchall()

    def _reversal_for(self, batch_id: str):
        return self.connection.execute(
            sql.SQL(
                "SELECT batch_id FROM {}.resolution_decision_batches WHERE reverses_batch_id = %s"
            ).format(self.schema),
            (batch_id,),
        ).fetchone()

    def _validate_reversal_heads(self, items) -> None:
        for item in items:
            if item[3] is None:
                current = self.writer.current_disposition(item[1])
            else:
                current = self.connection.execute(
                    sql.SQL(
                        "SELECT override_id FROM {}.binding_overrides WHERE override_id = %s AND retired_at IS NULL"
                    ).format(self.schema),
                    (item[6],),
                ).fetchone()
            if current is None or current[0] != item[6]:
                raise RuntimeError(
                    "a later decision changed this batch; reverse that decision first"
                )


@dataclass(frozen=True)
class _StoredToken:
    resolution_materialization_id: str
    snapshot_id: int
    binding_revision: int
    disposition_revision: int


@dataclass(frozen=True)
class _StoredDraft:
    current_anilist_id: int
    summary: str
