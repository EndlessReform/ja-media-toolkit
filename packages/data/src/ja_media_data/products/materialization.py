"""Framework-neutral context and result types for durable product commits."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MaterializationContext:
    """Lineage supplied to a product commit by any orchestration adapter."""

    attempt_id: str
    pipeline_run_id: str
    recipe_revision: str
    build_key: str
    input_heads: dict[str, object]


@dataclass(frozen=True)
class ProductCommitResult:
    """The durable outcome returned independently of an executor callback."""

    target: str
    written: bool
    fingerprint: str
    rows: int
    materialization_id: str
