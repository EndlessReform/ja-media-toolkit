"""Content fingerprints for rebuildable identity products."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import Sequence

from ja_media_data.resolution_types import CaptureObservation, ResolutionBatch


def fingerprint_observations(items: Sequence[CaptureObservation]) -> str:
    """Hash normalized capture evidence while excluding observation time."""

    rows = [
        {key: value for key, value in asdict(item).items() if key != "observed_at"}
        for item in items
    ]
    return _fingerprint(rows)


def fingerprint_resolution(batch: ResolutionBatch) -> str:
    """Hash exact automatic claims while excluding execution provenance."""

    rows = {
        "hints": [_without_run_source(item) for item in batch.hints],
        "bindings": [_without_run_source(item) for item in batch.bindings],
        "issues": [_without_run_source(item) for item in batch.issues],
    }
    return _fingerprint(rows)


def _without_run_source(value: object) -> dict[str, object]:
    record = asdict(value)
    record.pop("run_source", None)
    return record


def _fingerprint(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()
