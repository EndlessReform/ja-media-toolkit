"""Low-level PostgreSQL mutations shared by promotion and reversal."""

from __future__ import annotations

import uuid

from psycopg import sql

from ja_media_data.lakehouse.repository import DuckLakeRepository


class ResolutionControlWriter:
    """Write already-validated control heads inside a caller-owned transaction."""

    def __init__(self, repository: DuckLakeRepository, *, environment: str) -> None:
        overrides = repository.override_repository
        if overrides is None:
            raise RuntimeError("PostgreSQL control storage is not configured")
        self.overrides = overrides
        self.connection = overrides.connection
        self.schema = overrides.schema
        self.environment = environment

    def lock(self) -> None:
        self.connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            ("ja_media_binding_overrides",),
        )

    def revision(self, name: str) -> int:
        row = self.connection.execute(
            sql.SQL("SELECT revision FROM {}.control_revisions WHERE name = %s").format(
                self.schema
            ),
            (name,),
        ).fetchone()
        return int(row[0])

    def advance(self, name: str) -> int:
        row = self.connection.execute(
            sql.SQL(
                "UPDATE {}.control_revisions SET revision = revision + 1 "
                "WHERE name = %s RETURNING revision"
            ).format(self.schema),
            (name,),
        ).fetchone()
        return int(row[0])

    def insert_batch(
        self, *, batch_id, action, reverses, draft, reviewed, reason, revisions
    ) -> None:
        self.connection.execute(
            sql.SQL(
                """INSERT INTO {}.resolution_decision_batches (
                       batch_id, action, reverses_batch_id, environment,
                       current_anilist_id, summary, reason,
                       resolution_materialization_id, snapshot_id,
                       base_binding_revision, applied_binding_revision,
                       base_disposition_revision, applied_disposition_revision)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"""
            ).format(self.schema),
            (
                batch_id,
                action,
                reverses,
                self.environment,
                draft.current_anilist_id,
                draft.summary,
                reason,
                reviewed.resolution_materialization_id,
                reviewed.snapshot_id,
                *revisions,
            ),
        )

    def apply_row(
        self,
        batch_id: str,
        index: int,
        row: dict,
        binding_revision: int,
        disposition_revision: int,
    ) -> None:
        destination = row["destination_anilist_id"]
        if destination is None:
            prior = self.current_disposition(row["capture_id"])
            control_id = f"disposition-{uuid.uuid4().hex}"
            if prior:
                self.retire_disposition(row["capture_id"], disposition_revision)
            self.connection.execute(
                sql.SQL(
                    "INSERT INTO {}.capture_dispositions "
                    "(disposition_id, capture_id, disposition, decision_note, created_revision) "
                    "VALUES (%s, %s, %s, %s, %s)"
                ).format(self.schema),
                (
                    control_id,
                    row["capture_id"],
                    row["decision"],
                    row["rationale"],
                    disposition_revision,
                ),
            )
            self._insert_item(batch_id, index, row, control_id, None, prior)
            return
        prior = self.overrides.get_current_override(
            "anilist", str(destination), row["destination_episode"]
        )
        control_id = f"override-{uuid.uuid4().hex}"
        self.connection.execute(
            sql.SQL(
                "UPDATE {}.binding_overrides SET retired_at = now(), retired_revision = %s "
                "WHERE namespace = 'anilist' AND series_id = %s AND episode = %s "
                "AND retired_at IS NULL"
            ).format(self.schema),
            (binding_revision, str(destination), row["destination_episode"]),
        )
        self.connection.execute(
            sql.SQL(
                "INSERT INTO {}.binding_overrides "
                "(override_id, namespace, series_id, episode, audio_capture_id, "
                "decision_method, decision_note, created_revision) "
                "VALUES (%s, 'anilist', %s, %s, %s, 'agent-human-approved', %s, %s)"
            ).format(self.schema),
            (
                control_id,
                str(destination),
                row["destination_episode"],
                row["capture_id"],
                row["rationale"],
                binding_revision,
            ),
        )
        self._insert_item(batch_id, index, row, control_id, prior, None)

    def reverse_item(
        self, item, binding_revision: int, disposition_revision: int
    ) -> None:
        if item[3] is None:
            self.retire_disposition(item[1], disposition_revision)
            if item[10] is not None:
                self.connection.execute(
                    sql.SQL(
                        "INSERT INTO {}.capture_dispositions (disposition_id, capture_id, disposition, decision_note, created_revision) VALUES (%s, %s, %s, %s, %s)"
                    ).format(self.schema),
                    (
                        f"disposition-{uuid.uuid4().hex}",
                        item[1],
                        item[10],
                        item[11],
                        disposition_revision,
                    ),
                )
            return
        self.connection.execute(
            sql.SQL(
                "UPDATE {}.binding_overrides SET retired_at = now(), retired_revision = %s WHERE override_id = %s"
            ).format(self.schema),
            (binding_revision, item[6]),
        )
        if item[7] is not None or item[8] is not None:
            self.connection.execute(
                sql.SQL(
                    "INSERT INTO {}.binding_overrides (override_id, namespace, series_id, episode, audio_capture_id, decision_method, decision_note, created_revision) VALUES (%s, 'anilist', %s, %s, %s, %s, %s, %s)"
                ).format(self.schema),
                (
                    f"override-{uuid.uuid4().hex}",
                    str(item[3]),
                    item[4],
                    item[7],
                    item[8],
                    item[9],
                    binding_revision,
                ),
            )

    def current_disposition(self, capture_id: str):
        return self.connection.execute(
            sql.SQL(
                "SELECT disposition_id, disposition, decision_note FROM {}.capture_dispositions WHERE capture_id = %s AND retired_at IS NULL"
            ).format(self.schema),
            (capture_id,),
        ).fetchone()

    def retire_disposition(self, capture_id: str, revision: int) -> None:
        self.connection.execute(
            sql.SQL(
                "UPDATE {}.capture_dispositions SET retired_at = now(), retired_revision = %s WHERE capture_id = %s AND retired_at IS NULL"
            ).format(self.schema),
            (revision, capture_id),
        )

    def _insert_item(
        self, batch_id, index, row, control_id, prior, disposition
    ) -> None:
        self.connection.execute(
            sql.SQL(
                """INSERT INTO {}.resolution_decision_items (
                       batch_id, item_index, capture_id, decision,
                       destination_anilist_id, destination_episode, rationale,
                       applied_control_id, prior_capture_id, prior_method, prior_note,
                       prior_disposition, prior_disposition_note)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"""
            ).format(self.schema),
            (
                batch_id,
                index,
                row["capture_id"],
                row["decision"],
                row["destination_anilist_id"],
                row["destination_episode"],
                row["rationale"],
                control_id,
                prior.audio_capture_id if prior else None,
                prior.decision_method if prior else None,
                prior.decision_note if prior else None,
                disposition[1] if disposition else None,
                disposition[2] if disposition else None,
            ),
        )
