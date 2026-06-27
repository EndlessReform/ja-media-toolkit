"""Subsync-local vocal-separation orchestration config.

This is intentionally not part of the global app config model yet.  The TUI is
the first consumer, so it owns how separated stems are routed into its VAD track.
If transcribe or another tool grows the same shape later, extract this by the
rule of three rather than guessing the final pipeline abstraction now.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from ja_media_core.config import resolve_config_path


VocalSeparationBackendName = Literal["demucs"]


@dataclass(frozen=True)
class SubsyncVocalSeparationConfig:
    """TUI-owned routing policy for preparing a better VAD analysis source."""

    enabled: bool = True
    backend: VocalSeparationBackendName = "demucs"
    stem: str = "vocals"
    cache_dir: Path | None = None
    model: str = "htdemucs"
    device: str | None = None
    jobs: int | None = None
    segment_s: float | None = None

    @classmethod
    def load(cls) -> "SubsyncVocalSeparationConfig":
        """Load ``[subsync_tui.vocal_separation]`` from the normal TOML path."""

        path = resolve_config_path()
        if not path.exists():
            return cls()
        with path.open("rb") as config_file:
            data = tomllib.load(config_file)
        section = (
            data.get("subsync_tui", {})
            .get("vocal_separation", {})
        )
        if not isinstance(section, dict):
            raise ValueError("[subsync_tui.vocal_separation] must be a table")
        return cls.from_mapping(section)

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "SubsyncVocalSeparationConfig":
        backend = data.get("backend", "demucs")
        if backend != "demucs":
            raise ValueError(
                "Unsupported subsync vocal separation backend "
                f"{backend!r}; supported backends: demucs"
            )
        cache_dir = data.get("cache_dir")
        return cls(
            enabled=bool(data.get("enabled", True)),
            backend=backend,
            stem=str(data.get("stem", "vocals")),
            cache_dir=Path(cache_dir).expanduser() if cache_dir else None,
            model=str(data.get("model", "htdemucs")),
            device=_optional_str(data.get("device")),
            jobs=_optional_int(data.get("jobs")),
            segment_s=_optional_float(data.get("segment_s")),
        )


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)
