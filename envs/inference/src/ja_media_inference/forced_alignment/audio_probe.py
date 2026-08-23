"""Small ffprobe helpers for the dependency-light inference client."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess


def probe_audio_duration(path: Path) -> float:
    """Read a local audio duration without importing heavyweight media packages."""

    completed = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    duration_s = float(json.loads(completed.stdout)["format"]["duration"])
    if duration_s <= 0:
        raise ValueError("audio duration must be positive")
    return duration_s


def audio_edge_distance(time_s: float, duration_s: float) -> float:
    """Return zero outside the crop and distance to its nearest edge inside."""

    return max(0.0, min(time_s, duration_s - time_s))
