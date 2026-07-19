"""Versioned item contracts crossing the control-plane/compute boundary."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class WorkerModel(BaseModel):
    """Strict immutable base so contract drift fails before expensive compute."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ObjectRef(WorkerModel):
    """Immutable object identity supplied with least-privilege Garage access."""

    bucket: str
    key: str
    etag: str | None = None
    bytes: int | None = Field(default=None, ge=1)


class VadRequest(WorkerModel):
    """Everything an environment needs to segment one canonical episode."""

    operation: Literal["vad_segments"] = "vad_segments"
    locator: str
    source: ObjectRef
    output_prefix: str
    recipe_revision: str
    model_id: str
    split_every_minutes: int = Field(default=10, ge=1)
    split_radius_seconds: int = Field(default=60, ge=0)


WorkerRequest = Annotated[VadRequest, Field(discriminator="operation")]


class WorkEnvelope(WorkerModel):
    """Small retry-safe Celery payload; media and transcript bodies stay in Garage."""

    contract_version: Literal[1] = 1
    request_id: str
    campaign_run_id: str
    step_key: str
    attempt: int = Field(ge=1)
    requested_at: datetime
    payload: WorkerRequest


class VadChunk(WorkerModel):
    """One committed FLAC segment referenced by a marker-last result."""

    object: ObjectRef
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)


class VadResult(WorkerModel):
    """Verified-shape VAD output; the server still HEAD-checks every object."""

    operation: Literal["vad_segments"] = "vad_segments"
    locator: str
    output_fingerprint: str
    chunks: tuple[VadChunk, ...] = Field(min_length=1)
    elapsed_seconds: float = Field(ge=0)


WorkerResult = Annotated[VadResult, Field(discriminator="operation")]


class ResultEnvelope(WorkerModel):
    """Environment result returned locally and written as a temporary marker."""

    contract_version: Literal[1] = 1
    request_id: str
    status: Literal["succeeded"] = "succeeded"
    completed_at: datetime
    result: WorkerResult
