"""Read-only identities for durable domain-product materializations."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Mapping

import duckdb

from ja_media_data.lakehouse.time_travel import table_ref


@dataclass(frozen=True)
class ProductHead:
    """Newest committed identity for one corpus-scoped logical product."""

    materialization_id: str
    target: str
    fingerprint: str
    recipe_revision: str | None
    build_key: str | None
    run_id: str | None
    computed_at: object
    snapshot_id: int | None
    input_heads: Mapping[str, object]


class MaterializationCatalog:
    """Query product lineage without depending on an execution framework."""

    def __init__(self, connection: duckdb.DuckDBPyConnection) -> None:
        self.connection = connection

    def current_head(
        self, target: str, *, snapshot_id: int | None = None
    ) -> ProductHead | None:
        source = table_ref("materializations", snapshot_id=snapshot_id)
        row = self.connection.execute(
            """SELECT materialization_id, target, fingerprint, recipe_revision,
                      build_key, run_id, computed_at, snapshot_id, input_heads
               FROM """
            + source
            + """ WHERE target = ? AND scope = 'corpus'
               ORDER BY computed_at DESC, materialization_id DESC LIMIT 1""",
            [target],
        ).fetchone()
        if row is None:
            return None
        return ProductHead(
            materialization_id=str(row[0]),
            target=str(row[1]),
            fingerprint=str(row[2]),
            recipe_revision=str(row[3]) if row[3] else None,
            build_key=str(row[4]) if row[4] else None,
            run_id=str(row[5]) if row[5] else None,
            computed_at=row[6],
            snapshot_id=int(row[7]) if row[7] is not None else None,
            input_heads=decode_heads(row[8]),
        )

    def input_heads(
        self, *targets: str, snapshot_id: int | None = None
    ) -> dict[str, object]:
        """Return compact identities for explicitly declared product inputs."""

        heads: dict[str, object] = {}
        for target in targets:
            head = self.current_head(target, snapshot_id=snapshot_id)
            heads[target] = (
                {
                    "materialization_id": head.materialization_id,
                    "fingerprint": head.fingerprint,
                }
                if head
                else {"materialization_id": None, "fingerprint": None}
            )
        return heads

    def current_snapshot_id(self) -> int | None:
        """Return DuckLake's current snapshot for exact projection cache keys."""

        catalog = str(
            self.connection.execute("SELECT current_database()").fetchone()[0]
        )
        quoted = '"' + catalog.replace('"', '""') + '"'
        row = self.connection.execute(
            f"SELECT id FROM {quoted}.current_snapshot()"
        ).fetchone()
        return int(row[0]) if row else None


def structural_build_key(
    target: str, recipe_revision: str, input_heads: Mapping[str, object]
) -> str:
    """Hash declared dependency heads and recipe identity, not output content."""

    payload = json.dumps(
        {
            "target": target,
            "recipe_revision": recipe_revision,
            "input_heads": input_heads,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def decode_heads(value: object) -> dict[str, object]:
    """Normalize DuckDB JSON values into an ordinary mapping."""

    if value is None:
        return {}
    if isinstance(value, str):
        decoded = json.loads(value)
        return decoded if isinstance(decoded, dict) else {}
    return dict(value) if isinstance(value, Mapping) else {}
