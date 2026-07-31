"""Bounded Bronze-to-Kitsunekko subtitle comparison for resolution review."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from httpx import HTTPError
from ja_media_core.kitsunekko import KitsunekkoSubtitlesClient


SubtitleLister = Callable[[int, str], list[dict]]
SubtitleReader = Callable[[int, str, int, int, int], str]


class SubtitleComparator:
    """Compare one pinned capture stream with one proposed episode reference."""

    def __init__(
        self,
        *,
        kitsunekko: KitsunekkoSubtitlesClient | None,
        list_capture_subtitles: SubtitleLister,
        read_capture_subtitle: SubtitleReader,
    ) -> None:
        self._kitsunekko = kitsunekko
        self._list_capture_subtitles = list_capture_subtitles
        self._read_capture_subtitle = read_capture_subtitle
        self.attempts: dict[tuple[str, int, str], str] = {}

    def compare(
        self,
        current_anilist_id: int,
        capture_id: str,
        proposed_anilist_id: int,
        proposed_episode: int,
        *,
        capture_stream_index: int | None,
        reference_subtitle_id: str | None,
        start_line: int,
        line_count: int,
    ) -> dict:
        """Return equally bounded source/reference pages and record the attempt."""

        if proposed_anilist_id < 1 or proposed_episode < 1:
            raise ValueError("proposed AniList ID and integer episode must be positive")
        if start_line < 1 or not 1 <= line_count <= 100:
            raise ValueError(
                "comparison pages require start_line >= 1 and line_count 1..100"
            )

        streams = self._list_capture_subtitles(current_anilist_id, capture_id)
        stream = _select_capture_stream(streams, capture_stream_index)
        capture = {
            "capture_id": capture_id,
            "stream_index": stream["stream_index"],
            "filename": stream["filename"],
            "start_line": start_line,
            "line_count": line_count,
            "text": self._read_capture_subtitle(
                current_anilist_id,
                capture_id,
                int(stream["stream_index"]),
                start_line,
                line_count,
            ),
        }
        episode = str(proposed_episode)
        key = (capture_id, proposed_anilist_id, episode)
        if self._kitsunekko is None:
            self.attempts[key] = "service_unavailable"
            return _unavailable(
                "service_unavailable", capture, proposed_anilist_id, episode
            )

        try:
            response = self._kitsunekko.anilist_episode_files(
                proposed_anilist_id, proposed_episode
            )
            candidates = _supported_candidates(response.files)
            if not candidates:
                self.attempts[key] = "reference_unavailable"
                result = _unavailable(
                    "reference_unavailable", capture, proposed_anilist_id, episode
                )
                if response.files:
                    result.update(
                        availability="unsupported_format",
                        series_file_count=response.count,
                    )
                else:
                    series = self._kitsunekko.anilist_files(proposed_anilist_id)
                    result.update(
                        availability=(
                            "series_not_indexed"
                            if series.count == 0
                            else "episode_not_indexed"
                        ),
                        series_file_count=series.count,
                    )
                return result
            selected = _select_reference(candidates, reference_subtitle_id)
            content = self._kitsunekko.file_content(str(selected["subtitle_id"]))
        except (HTTPError, OSError, RuntimeError) as error:
            self.attempts[key] = "service_unavailable"
            result = _unavailable(
                "service_unavailable", capture, proposed_anilist_id, episode
            )
            result["message"] = str(error)
            return result

        lines = content.decode("utf-8-sig", errors="replace").splitlines()
        capture_has_more = len(capture["text"].splitlines()) == line_count
        reference_has_more = start_line - 1 + line_count < len(lines)
        self.attempts[key] = "compared"
        return {
            "status": "compared",
            "capture": capture,
            "reference": {
                "anilist_id": proposed_anilist_id,
                "episode": episode,
                "subtitle_id": selected["subtitle_id"],
                "filename": selected["filename"],
                "start_line": start_line,
                "line_count": line_count,
                "text": _numbered_page(lines, start_line, line_count),
            },
            "reference_candidates": [
                {
                    "subtitle_id": item["subtitle_id"],
                    "filename": item["filename"],
                }
                for item in candidates[:10]
            ],
            "next_start_line": (
                start_line + line_count
                if capture_has_more or reference_has_more
                else None
            ),
        }

    def status_for(self, capture_id: str, anilist_id: int, episode: str) -> str:
        """Return the transient exact-locator check shown in human review."""

        return self.attempts.get((capture_id, anilist_id, episode), "not_checked")


def _select_capture_stream(streams: list[dict], requested: int | None) -> dict:
    if requested is not None:
        matches = [item for item in streams if item["stream_index"] == requested]
        if len(matches) != 1:
            raise KeyError(f"capture has no unique subtitle stream {requested}")
        return matches[0]
    if not streams:
        raise ValueError("capture has no subtitle streams to compare")
    return min(
        streams,
        key=lambda item: (
            str(item.get("declared_language") or "").lower()
            not in {"ja", "jpn", "japanese"},
            int(item["stream_index"]),
        ),
    )


def _supported_candidates(files: tuple[dict, ...]) -> list[dict]:
    candidates = []
    for item in files:
        filename = str(item.get("filename") or item.get("repo_path") or "")
        extension = (
            str(item.get("extension") or Path(filename).suffix).lower().lstrip(".")
        )
        if item.get("subtitle_id") and extension in {"srt", "ass", "ssa"}:
            candidates.append({**item, "filename": filename, "extension": extension})
    return sorted(
        candidates, key=lambda item: (item["extension"] != "srt", item["filename"])
    )


def _select_reference(candidates: list[dict], requested: str | None) -> dict:
    if not requested or requested.lower() in {"auto", "default"}:
        return candidates[0]
    matches = [item for item in candidates if str(item["subtitle_id"]) == requested]
    if len(matches) != 1:
        raise ValueError(f"reference subtitle is not an episode candidate: {requested}")
    return matches[0]


def _numbered_page(lines: list[str], start_line: int, line_count: int) -> str:
    page = lines[start_line - 1 : start_line - 1 + line_count]
    return "\n".join(
        f"{number}: {line}" for number, line in enumerate(page, start=start_line)
    )


def _unavailable(status: str, capture: dict, anilist_id: int, episode: str) -> dict:
    return {
        "status": status,
        "capture": capture,
        "reference": {
            "anilist_id": anilist_id,
            "episode": episode,
            "text": "",
        },
        "reference_candidates": [],
    }
