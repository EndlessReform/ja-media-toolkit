"""Load the deliberately small set of retiming research cases."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tomllib


@dataclass(frozen=True)
class CanonicalCase:
    """One episode selected through a canonical namespace binding."""

    name: str
    namespace: str
    series_id: str
    episode: str
    subtitle_id: str | None = None
    subtitle_source_sha256: str | None = None
    cleaning_reconstruct: str | None = None
    bootstrap_candidate_id: str | None = None
    prior_identity_score: float | None = None


def load_case(path: Path, name: str) -> CanonicalCase:
    """Return one named case and reject duplicate or unsupported rows."""

    document = tomllib.loads(path.read_text())
    if document.get("schema_version") != 1:
        raise ValueError("cases.toml must use schema_version = 1")
    matches = [item for item in document.get("case", ()) if item.get("name") == name]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one case named {name!r}, found {len(matches)}")
    item = matches[0]
    namespace = str(item.get("namespace") or "").strip()
    series_id = str(item.get("series_id") or "").strip()
    episode = str(item.get("episode") or "").strip()
    if namespace != "anilist":
        raise ValueError("candidate pulls currently require namespace = 'anilist'")
    if not series_id.isdigit() or int(series_id) < 1:
        raise ValueError("series_id must be a positive AniList integer")
    if not episode.isdigit() or int(episode) < 1:
        raise ValueError("episode must be a positive integer")
    return CanonicalCase(
        name=name,
        namespace=namespace,
        series_id=series_id,
        episode=episode,
        subtitle_id=_optional_str(item.get("subtitle_id")),
        subtitle_source_sha256=_optional_str(item.get("subtitle_source_sha256")),
        cleaning_reconstruct=_optional_str(item.get("cleaning_reconstruct")),
        bootstrap_candidate_id=_optional_str(item.get("bootstrap_candidate_id")),
        prior_identity_score=_optional_float(item.get("prior_identity_score")),
    )


def _optional_str(value: object) -> str | None:
    return str(value) if value is not None else None


def _optional_float(value: object) -> float | None:
    return float(value) if value is not None else None
