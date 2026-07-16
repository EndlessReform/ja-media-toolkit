"""Transactional PostgreSQL control plane for human binding decisions.

Automatic resolver output is a replaceable DuckLake data product. Human
accept, correction, and unbind actions are different: they are small,
concurrent point decisions whose current-head uniqueness belongs in ordinary
PostgreSQL. This module deliberately uses psycopg directly; the single table
does not justify retaining an ORM or a migration framework.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator

import psycopg
from psycopg import sql

from ja_media_data.resolution_types import BindingConflictError


_PACKAGED_SCHEMA_DIR = Path(__file__).parent / "postgres_schema"
POSTGRES_SCHEMA_DIR = (
    _PACKAGED_SCHEMA_DIR
    if _PACKAGED_SCHEMA_DIR.is_dir()
    else Path(__file__).parents[2] / "postgres_schema"
)
DEFAULT_CONTROL_SCHEMA = "ja_media_control"


@dataclass(frozen=True)
class BindingOverrideRecord:
    """One active or historical human decision for an episode locator."""

    override_id: str
    namespace: str
    series_id: str
    episode: str
    audio_capture_id: str | None
    decision_method: str
    decision_note: str | None
    created_at: datetime
    retired_at: datetime | None


def postgres_url_for_psycopg(value: str) -> str:
    """Normalize the configured PostgreSQL URL for psycopg."""

    return value.replace("postgresql+psycopg://", "postgresql://", 1)


def apply_postgres_schema(
    connection: psycopg.Connection[tuple],
    *,
    control_schema: str = DEFAULT_CONTROL_SCHEMA,
    schema_dir: Path = POSTGRES_SCHEMA_DIR,
) -> list[str]:
    """Apply checksum-protected control-plane SQL in its own PostgreSQL schema."""

    identifier = sql.Identifier(control_schema)
    with connection.transaction():
        connection.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(identifier))
        connection.execute(
            sql.SQL(
                """CREATE TABLE IF NOT EXISTS {}.schema_history (
                       filename TEXT PRIMARY KEY,
                       checksum TEXT NOT NULL,
                       applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
                   )"""
            ).format(identifier)
        )
    applied = dict(
        connection.execute(
            sql.SQL("SELECT filename, checksum FROM {}.schema_history").format(
                identifier
            )
        ).fetchall()
    )
    newly_applied: list[str] = []
    for path in sorted(schema_dir.glob("[0-9][0-9][0-9]_*.sql")):
        contents = path.read_text()
        checksum = hashlib.sha256(contents.encode()).hexdigest()
        if existing := applied.get(path.name):
            if existing != checksum:
                raise RuntimeError(f"applied PostgreSQL schema file changed: {path.name}")
            continue
        with connection.transaction():
            connection.execute(
                sql.SQL("SET LOCAL search_path TO {}, pg_catalog").format(identifier)
            )
            connection.execute(contents)
            connection.execute(
                sql.SQL(
                    "INSERT INTO {}.schema_history (filename, checksum) VALUES (%s, %s)"
                ).format(identifier),
                (path.name, checksum),
            )
        newly_applied.append(path.name)
    return newly_applied


class BindingOverrideRepository:
    """Reads and atomically advances PostgreSQL binding-override heads."""

    def __init__(
        self,
        connection: psycopg.Connection[tuple],
        *,
        control_schema: str = DEFAULT_CONTROL_SCHEMA,
    ) -> None:
        if not connection.autocommit:
            raise ValueError(
                "binding override connections must use autocommit; writes open "
                "their own explicit transactions"
            )
        self.connection = connection
        self.table = sql.Identifier(control_schema, "binding_overrides")

    def append_override(
        self,
        *,
        namespace: str,
        series_id: str,
        episode: str,
        audio_capture_id: str | None,
        decision_method: str,
        decision_note: str | None = None,
        override_id: str | None = None,
    ) -> str:
        """Retire one locator head and append its replacement atomically.

        A NULL capture is an explicit unbind. The advisory transaction lock is
        intentionally coarse: human decisions are rare, and serializing this
        tiny critical section lets PostgreSQL constraints remain the complete
        concurrency policy.
        """

        identifier = override_id or f"override-{uuid.uuid4().hex}"
        try:
            with self.connection.transaction():
                self.connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    ("ja_media_binding_overrides",),
                )
                current = self._get_current(namespace, series_id, episode)
                if current is not None and (
                    current.audio_capture_id,
                    current.decision_method,
                    current.decision_note,
                ) == (audio_capture_id, decision_method, decision_note):
                    return current.override_id
                self.connection.execute(
                    sql.SQL(
                        """UPDATE {} SET retired_at = now()
                           WHERE namespace = %s AND series_id = %s AND episode = %s
                             AND retired_at IS NULL"""
                    ).format(self.table),
                    (namespace, series_id, episode),
                )
                self.connection.execute(
                    sql.SQL(
                        """INSERT INTO {} (
                               override_id, namespace, series_id, episode,
                               audio_capture_id, decision_method, decision_note
                           ) VALUES (%s, %s, %s, %s, %s, %s, %s)"""
                    ).format(self.table),
                    (
                        identifier,
                        namespace,
                        series_id,
                        episode,
                        audio_capture_id,
                        decision_method,
                        decision_note,
                    ),
                )
        except psycopg.errors.UniqueViolation as error:
            raise BindingConflictError(
                "capture already has a different active PostgreSQL override"
            ) from error
        return identifier

    def get_current_override(
        self, namespace: str, series_id: str, episode: str
    ) -> BindingOverrideRecord | None:
        """Return the active locator override, including an explicit unbind."""

        return self._get_current(namespace, series_id, episode)

    def get_current_override_for_capture(
        self, capture_id: str
    ) -> BindingOverrideRecord | None:
        """Return the active override assigning one capture, if present."""

        row = self.connection.execute(
            sql.SQL(
                """SELECT override_id, namespace, series_id, episode,
                          audio_capture_id, decision_method, decision_note,
                          created_at, retired_at
                   FROM {} WHERE audio_capture_id = %s AND retired_at IS NULL"""
            ).format(self.table),
            (capture_id,),
        ).fetchone()
        return BindingOverrideRecord(*row) if row else None

    def iter_current_overrides(self) -> Iterator[BindingOverrideRecord]:
        """Yield the small active decision set for effective-view summaries."""

        rows = self.connection.execute(
            sql.SQL(
                """SELECT override_id, namespace, series_id, episode,
                          audio_capture_id, decision_method, decision_note,
                          created_at, retired_at
                   FROM {} WHERE retired_at IS NULL
                   ORDER BY namespace, series_id, episode"""
            ).format(self.table)
        ).fetchall()
        yield from (BindingOverrideRecord(*row) for row in rows)

    def _get_current(
        self, namespace: str, series_id: str, episode: str
    ) -> BindingOverrideRecord | None:
        row = self.connection.execute(
            sql.SQL(
                """SELECT override_id, namespace, series_id, episode,
                          audio_capture_id, decision_method, decision_note,
                          created_at, retired_at
                   FROM {} WHERE namespace = %s AND series_id = %s
                     AND episode = %s AND retired_at IS NULL"""
            ).format(self.table),
            (namespace, series_id, episode),
        ).fetchone()
        return BindingOverrideRecord(*row) if row else None


class EffectiveBindingOverrideWriter:
    """Mixin that validates DuckLake capture state before a PG decision."""

    override_repository: BindingOverrideRepository | None

    def get_capture(self, capture_id: str) -> object | None:
        raise NotImplementedError

    def get_current_binding_for_capture(self, capture_id: str) -> object | None:
        raise NotImplementedError

    def append_override(
        self,
        *,
        namespace: str,
        series_id: str,
        episode: str,
        audio_capture_id: str | None,
        decision_method: str = "human",
        decision_note: str | None = None,
        override_id: str | None = None,
    ) -> str:
        """Validate the cross-store reference, then transact the PG head."""

        if self.override_repository is None:
            raise RuntimeError("PostgreSQL binding overrides are not configured")
        if audio_capture_id is not None:
            if self.get_capture(audio_capture_id) is None:
                raise LookupError(f"capture {audio_capture_id!r} is not indexed")
            current = self.get_current_binding_for_capture(audio_capture_id)
            locator = (namespace, series_id, episode)
            if current is not None and (
                current.namespace,
                current.series_id,
                current.episode,
            ) != locator:
                raise BindingConflictError(
                    "capture is currently bound to another episode; unbind it first"
                )
        return self.override_repository.append_override(
            namespace=namespace,
            series_id=series_id,
            episode=episode,
            audio_capture_id=audio_capture_id,
            decision_method=decision_method,
            decision_note=decision_note,
            override_id=override_id,
        )


def control_schema_from_env(*, catalog_schema: str | None = None) -> str:
    """Return the non-DuckLake PostgreSQL schema used for operator decisions."""

    control_schema = os.environ.get("JA_MEDIA_CONTROL_SCHEMA", DEFAULT_CONTROL_SCHEMA)
    if control_schema == catalog_schema:
        raise ValueError("PostgreSQL control and DuckLake catalog schemas must differ")
    return control_schema
