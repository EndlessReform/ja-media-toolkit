from __future__ import annotations

import glob
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from ja_media_core.subsync import is_supported_subtitle_file


def resolve_subtitle_inputs(
    inputs: Iterable[str],
    *,
    allow_empty: bool = False,
) -> list[Path]:
    """Expand paths and quoted globs into unique SRT/ASS files.

    Ordering is deterministic: arguments retain CLI order and matches within a
    glob are sorted. Duplicate resolved paths are returned only once.
    """

    paths: list[Path] = []
    seen: set[Path] = set()
    for raw_input in inputs:
        expanded = str(Path(raw_input).expanduser())
        if glob.has_magic(expanded):
            matches = sorted(
                Path(match).resolve()
                for match in glob.glob(expanded, recursive=True)
                if Path(match).is_file()
            )
            if not matches:
                raise ValueError(f"No subtitle files matched pattern: {raw_input}")
        else:
            matches = [Path(expanded).resolve()]

        for path in matches:
            if path in seen:
                continue
            if not path.is_file():
                raise ValueError(f"Subtitle input is not a file: {path}")
            if not is_supported_subtitle_file(path):
                raise ValueError(
                    f"Unsupported subtitle input {path}; expected .srt or .ass"
                )
            seen.add(path)
            paths.append(path)

    if not paths and not allow_empty:
        raise ValueError("No subtitle candidates were provided")
    return paths


def discover_subtitle_file(
    media_path: str | Path,
    *,
    sub_file: str | Path | None,
) -> Path:
    """Resolve one local subtitle, preferring an exact-stem sidecar.

    Explicit files may be SRT or ASS. Automatic discovery checks exact
    ``.srt`` and ``.ass`` sidecars first, then accepts exactly one supported
    sibling whose name begins with the media stem. Ambiguity is reported rather
    than silently binding the reader to the wrong language or release.
    """

    # Preserve the caller's path spelling here. On macOS, resolving a
    # ``/var`` temporary path rewrites it to ``/private/var`` and makes a
    # discovered sibling compare unequal to the path the caller supplied.
    media = Path(media_path).expanduser()
    if sub_file is not None:
        paths = resolve_subtitle_inputs([str(sub_file)])
        return paths[0]

    exact_matches = [
        candidate
        for suffix in (".srt", ".ass")
        if (candidate := media.with_suffix(suffix)).is_file()
    ]
    if len(exact_matches) == 1:
        return exact_matches[0]
    if len(exact_matches) > 1:
        _raise_ambiguous_subtitles(exact_matches)

    matches = sorted(
        path
        for path in media.parent.glob(f"{media.stem}*")
        if path.is_file() and is_supported_subtitle_file(path)
    )
    if not matches:
        raise ValueError(
            "Could not autodiscover a subtitle sidecar. Expected "
            f"{media.with_suffix('.srt')} or {media.with_suffix('.ass')}, "
            "or pass --sub-file."
        )
    if len(matches) > 1:
        _raise_ambiguous_subtitles(matches)
    return matches[0]


def is_supported_remote_subtitle(file: Mapping[str, Any]) -> bool:
    """Return whether a Kitsunekko row describes an SRT or ASS file."""

    extension = str(file.get("extension", "")).lower().lstrip(".")
    if extension:
        return extension in {"srt", "ass"}
    filename = str(file.get("filename") or file.get("repo_path") or "")
    return is_supported_subtitle_file(filename)


def search_remote_subtitles(
    files: Iterable[Mapping[str, Any]],
    query: str,
    *,
    limit: int | None = None,
) -> list[Mapping[str, Any]]:
    """Filter supported remote rows and rank them by fuzzy text match."""

    supported = [file for file in files if is_supported_remote_subtitle(file)]
    if query.strip():
        supported.sort(key=lambda file: _remote_match_sort_key(file, query))
    if limit is not None:
        return supported[:limit]
    return supported


def sidecar_path(media_path: str | Path) -> Path:
    """Return the conventional SRT sidecar destination for a media file."""

    return Path(media_path).expanduser().resolve().with_suffix(".srt")


def _raise_ambiguous_subtitles(paths: Sequence[Path]) -> None:
    match_list = "\n".join(f"  {path}" for path in paths)
    raise ValueError(
        "Multiple SRT sidecars matched the media stem; pass --sub-file:\n"
        f"{match_list}"
    )


def _remote_match_sort_key(
    file: Mapping[str, Any],
    query: str,
) -> tuple[int, str]:
    haystack = _remote_search_text(file)
    return (-_fuzzy_score(query, haystack), haystack)


def _remote_search_text(file: Mapping[str, Any]) -> str:
    parts = [
        file.get("filename"),
        file.get("repo_path"),
        file.get("group_hint"),
        file.get("language_hint"),
        file.get("episode_raw"),
        file.get("episode_local"),
        file.get("episode_absolute"),
    ]
    parts.extend(file.get("release_tags") or [])
    return " ".join(str(part) for part in parts if part is not None).lower()


def _fuzzy_score(query: str, haystack: str) -> int:
    """Score an ordered subsequence while favoring compact matches."""

    needle = query.strip().lower()
    if not needle:
        return 0
    index = 0
    score = 0
    streak = 0
    for char in haystack:
        if index >= len(needle):
            break
        if char == needle[index]:
            index += 1
            streak += 1
            score += 10 + streak
        elif char.isspace() or char in "._-/[]()":
            streak = 0
        else:
            streak = max(0, streak - 1)
    if index < len(needle):
        return -10_000 + index
    if needle in haystack:
        score += 100
    return score - max(0, len(haystack) - len(needle)) // 20
