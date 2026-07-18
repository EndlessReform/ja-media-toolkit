"""Safe internal SQL fragments for current and DuckLake time-travel reads."""

from __future__ import annotations

import re


_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")


def table_ref(
    table: str, *, snapshot_id: int | None = None, alias: str | None = None
) -> str:
    """Return one trusted table reference optionally pinned to a snapshot."""

    if not _IDENTIFIER.fullmatch(table):
        raise ValueError(f"unsafe internal table name: {table}")
    if alias is not None and not _IDENTIFIER.fullmatch(alias):
        raise ValueError(f"unsafe internal table alias: {alias}")
    if snapshot_id is not None:
        if snapshot_id < 0:
            raise ValueError("snapshot id must be non-negative")
        reference = f"{table} AT (VERSION => {snapshot_id})"
        return f"(SELECT * FROM {reference}) AS {alias}" if alias else reference
    return f"{table} AS {alias}" if alias else table
