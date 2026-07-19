"""Transport-neutral invocation of an environment-owned worker command.

This module intentionally imports no Dagster, Celery, FastAPI, or catalog code.
Dispatchers may call it from Dagster/Celery today or Slurm/Modal later.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
import os
from pathlib import Path
import subprocess
import tempfile

from ja_media_data.workers.contracts import ResultEnvelope, WorkEnvelope


_FORBIDDEN_ENV = {
    "DAGSTER_POSTGRES_URL",
    "JA_MEDIA_DATA_DATABASE_URL",
    "JA_MEDIA_CONTROL_DATABASE_URL",
}


def invoke_environment(
    envelope: WorkEnvelope,
    command: Sequence[str],
    *,
    environment: Mapping[str, str] | None = None,
    timeout_seconds: float | None = None,
) -> ResultEnvelope:
    """Execute one request through a local ephemeral file and parse its result."""

    if not command:
        raise ValueError("environment command must not be empty")
    supplied = dict(environment or {})
    forbidden = sorted(_FORBIDDEN_ENV.intersection(supplied))
    if forbidden:
        raise ValueError(
            "environment command may not receive control-plane credentials: "
            + ", ".join(forbidden)
        )
    with tempfile.TemporaryDirectory(prefix="ja-media-work-") as directory:
        request_path = Path(directory) / "request.json"
        request_path.write_text(envelope.model_dump_json())
        process_env = {
            key: value for key, value in os.environ.items() if key not in _FORBIDDEN_ENV
        }
        process_env.update(supplied)
        process_env["JA_MEDIA_WORK_REQUEST"] = str(request_path)
        completed = subprocess.run(
            list(command), check=True, text=True, capture_output=True,
            env=process_env, timeout=timeout_seconds,
        )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError("environment command did not return one JSON result") from error
    return ResultEnvelope.model_validate(payload)
