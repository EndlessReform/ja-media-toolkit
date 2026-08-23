from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any


def write_execution_manifest(
    output_path: Path,
    *,
    provider: str,
    requested_model: str,
    rows: list[dict[str, Any]],
) -> Path:
    """Record both the requested model and names returned by the provider."""

    served_models = sorted(set(_served_models(rows)))
    path = output_path.with_suffix(".execution.json")
    path.write_text(
        json.dumps(
            {
                "schema_name": "ja-media.srt-clean.execution",
                "schema_version": "1.0.0",
                "completed_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "provider": provider,
                "requested_model": requested_model,
                "served_models": served_models,
                "requests": len(rows),
                "results_path": str(output_path),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _served_models(rows: list[dict[str, Any]]):
    for row in rows:
        bodies = [row.get("response", {}).get("body")]
        attempts = row.get("error", {}).get("attempts", [])
        bodies.extend(
            attempt.get("response", {}).get("body")
            for attempt in attempts
            if isinstance(attempt, dict)
        )
        for body in bodies:
            if isinstance(body, dict) and body.get("model"):
                yield str(body["model"])
