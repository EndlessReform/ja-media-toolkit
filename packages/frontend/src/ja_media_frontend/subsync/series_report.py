"""Reduce an existing series inventory into episode counts on the client."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class EpisodeCounts:
    """File availability and named groups for one local episode number."""

    episode: int
    releases: int
    groups: int
    unnamed: int


@dataclass(frozen=True)
class SeriesReport:
    """Presentation result; unknown aired counts remain distinct from zero."""

    anilist_id: int
    title: str
    status: str
    planned: int | None
    aired: int | None
    frontier_estimated: bool
    episodes: tuple[EpisodeCounts, ...]
    unassigned: int

    @property
    def covered(self) -> int:
        return sum(row.releases > 0 for row in self._summary_rows())

    @property
    def multiple_groups(self) -> int:
        return sum(row.groups > 1 for row in self._summary_rows())

    def _summary_rows(self) -> Iterable[EpisodeCounts]:
        return (row for row in self.episodes if self.aired is None or row.episode <= self.aired)


def positive_int(value: Any) -> int | None:
    """Accept whole positive episode numbers without truncating fractions."""
    if isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not number.is_finite() or number <= 0 or number != number.to_integral_value():
        return None
    return int(number)


def build_report(
    anilist_id: int,
    metadata: Mapping[str, Any],
    files: Iterable[Mapping[str, Any]],
    *,
    now: float,
) -> SeriesReport:
    """Use supplied episode parses and schedule metadata without reparsing files."""
    status = str(metadata.get("status") or "UNKNOWN")
    planned = positive_int(metadata.get("episodes"))
    aired = None
    estimated = False
    if status == "FINISHED":
        aired = planned
    elif status == "NOT_YET_RELEASED":
        aired = 0
    elif status == "RELEASING":
        next_airing = metadata.get("nextAiringEpisode")
        if isinstance(next_airing, dict):
            episode = positive_int(next_airing.get("episode"))
            timestamp = positive_int(next_airing.get("airingAt"))
            if episode and timestamp and timestamp > now:
                if planned is None or episode - 1 <= planned:
                    aired = episode - 1
                    estimated = True

    buckets: dict[int, list[Mapping[str, Any]]] = {}
    seen: set[str] = set()
    unassigned = 0
    for index, file in enumerate(files):
        identity = str(file.get("subtitle_id") or file.get("repo_path") or f"row:{index}")
        if identity in seen:
            continue
        seen.add(identity)
        episode = positive_int(file.get("episode_local"))
        if episode is None:
            unassigned += 1
        else:
            buckets.setdefault(episode, []).append(file)

    rows = []
    for episode, members in sorted(buckets.items()):
        groups = {str(file.get("group_hint") or "").strip().casefold() for file in members}
        unnamed = sum(not str(file.get("group_hint") or "").strip() for file in members)
        groups.discard("")
        rows.append(EpisodeCounts(episode, len(members), len(groups), unnamed))
    title = next(
        (str(metadata[key]) for key in ("title_romaji", "title_english", "title_native") if metadata.get(key)),
        f"AniList {anilist_id}",
    )
    return SeriesReport(anilist_id, title, status, planned, aired, estimated, tuple(rows), unassigned)
