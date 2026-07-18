"""Durable identities for global runs and independently committed stages.

A pipeline run is an operator dispatch, not a database transaction. Each stage
checkpoint commits atomically and may therefore advance the live workspace even
when a later checkpoint fails. These records make that mixed-generation state
explicit and allow the operator UI to distinguish output lineage from attempts.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Mapping


@dataclass(frozen=True)
class ProductHead:
    """Latest committed identity for one corpus-scoped product."""

    materialization_id: str
    target: str
    fingerprint: str
    recipe_revision: str | None
    build_key: str | None
    run_id: str | None
    computed_at: object
    snapshot_id: int | None
    input_heads: Mapping[str, object]


@dataclass(frozen=True)
class StageExecution:
    """One stage checkpoint belonging to a global pipeline run."""

    pipeline_run_id: str
    checkpoint_id: str
    attempt_id: str
    stage: str
    ordinal: int
    recipe_revision: str
    build_key: str
    input_heads: Mapping[str, object]


def structural_build_key(
    target: str, recipe_revision: str, input_heads: Mapping[str, object]
) -> str:
    """Hash only declared dependency heads and recipe identity.

    Product content fingerprints remain separate. This key answers whether the
    current product was validated against the inputs that are current now.
    """

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


def encode_heads(input_heads: Mapping[str, object]) -> str:
    """Serialize dependency heads deterministically for durable metadata."""

    return json.dumps(input_heads, ensure_ascii=False, sort_keys=True)


def decode_heads(value: object) -> dict[str, object]:
    """Normalize DuckDB JSON values into an ordinary mapping."""

    if value is None:
        return {}
    if isinstance(value, str):
        decoded = json.loads(value)
        return decoded if isinstance(decoded, dict) else {}
    return dict(value) if isinstance(value, Mapping) else {}
