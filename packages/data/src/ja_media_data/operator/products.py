"""Surface-neutral product identity and truthful status facets."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict


class ContractModel(BaseModel):
    """Immutable, strict base for planner and application contracts."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class SubjectKey(ContractModel):
    """Semantic grain and stable identifier for one product subject."""

    kind: str
    key: str


class ProductKey(ContractModel):
    """Logical product identity independent of its physical storage table."""

    product_type: str
    subject: SubjectKey
    variant: str | None = None
    recipe_revision: str | None = None

    @property
    def stable_id(self) -> str:
        """Return a compact deterministic identity for logs and JSON clients."""

        parts = (
            self.product_type,
            self.subject.kind,
            self.subject.key,
            self.variant or "",
            self.recipe_revision or "",
        )
        return "/".join(quote(part, safe=":._-@") for part in parts)


class Implementation(StrEnum):
    IMPLEMENTED = "implemented"
    NOT_IMPLEMENTED = "not_implemented"


class Applicability(StrEnum):
    APPLICABLE = "applicable"
    NOT_APPLICABLE = "not_applicable"
    UNKNOWN = "unknown"


class Currency(StrEnum):
    MISSING = "missing"
    CURRENT = "current"
    STALE = "stale"
    UNKNOWN = "unknown"


class Readiness(StrEnum):
    READY = "ready"
    BLOCKED_INPUTS = "blocked_inputs"
    BLOCKED_APPROVAL = "blocked_approval"
    BLOCKED_CAPABILITY = "blocked_capability"


class Execution(StrEnum):
    IDLE = "idle"
    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ProductObservation(ContractModel):
    """Projection of an authoritative domain row into common status facets."""

    key: ProductKey
    implementation: Implementation = Implementation.IMPLEMENTED
    applicability: Applicability = Applicability.APPLICABLE
    currency: Currency = Currency.MISSING
    readiness: Readiness = Readiness.BLOCKED_INPUTS
    execution: Execution = Execution.IDLE
    fingerprint: str | None = None
    observed_at: datetime | None = None
    run_id: str | None = None
    reason: str | None = None
