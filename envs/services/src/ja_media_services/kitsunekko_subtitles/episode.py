from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import PTN


INTEGER_RE = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")


def _positive_int(value: Any) -> int | None:
    """Return a positive integer episode number from a scalar parser value."""

    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.isdigit():
        parsed = int(value)
        return parsed if parsed > 0 else None
    return None


def _episode_numbers(value: Any) -> tuple[int, ...]:
    """Normalize PTN's flexible ``episode`` field into comparable integers.

    PTN normally returns a single integer, but multi-episode filenames can come
    back as lists or delimiter-separated strings. Keeping this normalization at
    the API edge lets the generated SQLite index stay stable while the filename
    parser remains replaceable.
    """

    parsed = _positive_int(value)
    if parsed is not None:
        return (parsed,)

    if isinstance(value, (list, tuple, set)):
        numbers: list[int] = []
        for item in value:
            numbers.extend(_episode_numbers(item))
        return tuple(dict.fromkeys(numbers))

    if isinstance(value, str):
        if re.search(r"\d+\.\d+", value):
            return ()
        return tuple(dict.fromkeys(int(match.group(1)) for match in INTEGER_RE.finditer(value)))

    return ()


@lru_cache(maxsize=50000)
def runtime_episode_numbers(filename: str) -> tuple[int, ...]:
    """Parse local episode numbers from one subtitle filename with PTN."""

    parsed = PTN.parse(Path(filename).stem)
    return _episode_numbers(parsed.get("episode"))


def matches_runtime_episode(file: dict[str, Any], episode_number: int) -> bool:
    """Return whether a subtitle row's filename belongs to the requested episode."""

    if episode_number <= 0:
        return False
    return episode_number in runtime_episode_numbers(str(file["filename"]))


def filter_files_by_runtime_episode(
    files: list[dict[str, Any]],
    episode_number: int,
    *,
    prefix: str | None = None,
) -> list[dict[str, Any]]:
    """Filter indexed rows by runtime-parsed episode number and optional path prefix."""

    return [
        file
        for file in files
        if (not prefix or str(file["repo_path"]).startswith(prefix))
        and matches_runtime_episode(file, episode_number)
    ]
