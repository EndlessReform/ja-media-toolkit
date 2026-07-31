"""Read models for durable resolution decision batches."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from psycopg import sql

from ja_media_data.lakehouse.repository import DuckLakeRepository


@dataclass(frozen=True)
class DecisionItem:
    capture_id: str
    decision: str
    destination_anilist_id: int | None
    destination_episode: str | None
    rationale: str


@dataclass(frozen=True)
class DecisionBatch:
    """One accepted draft or deterministic reversal shown in review history."""

    batch_id: str
    action: str
    reverses_batch_id: str | None
    environment: str
    current_anilist_id: int
    summary: str
    reason: str | None
    binding_revision: int
    disposition_revision: int
    created_at: datetime
    reversed_by: str | None
    items: tuple[DecisionItem, ...]

    @property
    def item_count(self) -> int:
        return len(self.items)


@dataclass(frozen=True)
class ResolvedSeriesSummary:
    """One source series with at least one active accepted decision batch."""

    anilist_id: int
    decision_count: int
    batch_count: int


class ResolutionDecisionHistory:
    """Bounded audit reads over the ordinary PostgreSQL control schema."""

    def __init__(self, repository: DuckLakeRepository) -> None:
        if repository.override_repository is None:
            raise RuntimeError("PostgreSQL control storage is not configured")
        self.connection = getattr(repository.override_repository, "connection", None)
        self.schema = getattr(repository.override_repository, "schema", None)

    def recent(self, *, limit: int = 50) -> tuple[DecisionBatch, ...]:
        rows = self.connection.execute(
            sql.SQL(
                """SELECT batch.batch_id, batch.action, batch.reverses_batch_id,
                          batch.environment, batch.current_anilist_id, batch.summary,
                          batch.reason, batch.applied_binding_revision,
                          batch.applied_disposition_revision, batch.created_at,
                          reversal.batch_id
                     FROM {}.resolution_decision_batches AS batch
                LEFT JOIN {}.resolution_decision_batches AS reversal
                       ON reversal.reverses_batch_id = batch.batch_id
                 ORDER BY batch.created_at DESC LIMIT %s"""
            ).format(self.schema, self.schema),
            (limit,),
        ).fetchall()
        return tuple(DecisionBatch(*row, self._items(row[0])) for row in rows)

    def active_for_series(self, anilist_id: int) -> tuple[DecisionBatch, ...]:
        """Return accepted, unreversed batches for one original series."""

        rows = self.connection.execute(
            sql.SQL(
                """SELECT batch.batch_id, batch.action, batch.reverses_batch_id,
                          batch.environment, batch.current_anilist_id, batch.summary,
                          batch.reason, batch.applied_binding_revision,
                          batch.applied_disposition_revision, batch.created_at,
                          reversal.batch_id
                     FROM {}.resolution_decision_batches AS batch
                LEFT JOIN {}.resolution_decision_batches AS reversal
                       ON reversal.reverses_batch_id = batch.batch_id
                    WHERE batch.action = 'accept'
                      AND batch.current_anilist_id = %s
                      AND reversal.batch_id IS NULL
                 ORDER BY batch.created_at DESC"""
            ).format(self.schema, self.schema),
            (anilist_id,),
        ).fetchall()
        return tuple(DecisionBatch(*row, self._items(row[0])) for row in rows)

    def resolved_series(
        self, *, offset: int = 0, limit: int = 50
    ) -> tuple[tuple[ResolvedSeriesSummary, ...], int]:
        """Page original series that still have an active accepted batch."""

        base = sql.SQL(
            """FROM {}.resolution_decision_batches AS batch
          LEFT JOIN {}.resolution_decision_batches AS reversal
                 ON reversal.reverses_batch_id = batch.batch_id
          LEFT JOIN {}.resolution_decision_items AS item
                 ON item.batch_id = batch.batch_id
              WHERE batch.action = 'accept' AND reversal.batch_id IS NULL"""
        ).format(self.schema, self.schema, self.schema)
        total = self.connection.execute(
            sql.SQL("SELECT count(DISTINCT batch.current_anilist_id) ") + base
        ).fetchone()[0]
        rows = self.connection.execute(
            sql.SQL(
                """SELECT batch.current_anilist_id, count(item.item_index),
                          count(DISTINCT batch.batch_id) """
            )
            + base
            + sql.SQL(
                """ GROUP BY batch.current_anilist_id
                     ORDER BY max(batch.created_at) DESC
                     LIMIT %s OFFSET %s"""
            ),
            (limit, offset),
        ).fetchall()
        return tuple(ResolvedSeriesSummary(*row) for row in rows), int(total)

    def disposed_capture_ids(self) -> set[str]:
        """Return captures dismissed from the operator's unresolved-issue queue.

        A disposition is a review convenience, not a canonicalization control:
        it hides an acknowledged extra while it remains an automatic resolution
        issue. It does not veto a future automatic proposal if resolver inputs or
        policy later classify that capture successfully.
        """

        if self.connection is None or self.schema is None:
            return set()
        rows = self.connection.execute(
            sql.SQL(
                "SELECT capture_id FROM {}.capture_dispositions "
                "WHERE retired_at IS NULL"
            ).format(self.schema)
        ).fetchall()
        return {str(row[0]) for row in rows}

    def resolved_capture_ids(self) -> set[str]:
        """Return captures removed from the raw rejection queue by human control."""

        captures = self.disposed_capture_ids()
        if self.connection is None or self.schema is None:
            return captures
        rows = self.connection.execute(
            sql.SQL(
                "SELECT audio_capture_id FROM {}.binding_overrides "
                "WHERE retired_at IS NULL AND audio_capture_id IS NOT NULL"
            ).format(self.schema)
        ).fetchall()
        captures.update(str(row[0]) for row in rows)
        return captures

    def _items(self, batch_id: str) -> tuple[DecisionItem, ...]:
        rows = self.connection.execute(
            sql.SQL(
                """SELECT capture_id, decision, destination_anilist_id,
                          destination_episode, rationale
                     FROM {}.resolution_decision_items
                    WHERE batch_id = %s ORDER BY item_index"""
            ).format(self.schema),
            (batch_id,),
        ).fetchall()
        return tuple(DecisionItem(*row) for row in rows)
