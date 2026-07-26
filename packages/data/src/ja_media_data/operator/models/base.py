"""Shared strict model behavior for operator application contracts."""

from pydantic import BaseModel, ConfigDict


class OperatorModel(BaseModel):
    """Reject accidental response-shape drift and make projections immutable."""

    model_config = ConfigDict(extra="forbid", frozen=True)
