from __future__ import annotations

from pathlib import Path

from ja_media_core.audio_library import SourceMediaProbe
from ja_media_frontend.audio_library.mapping import resolve_episode_keys


def _probe(filename: str) -> SourceMediaProbe:
    return SourceMediaProbe(
        path=Path(filename),
        duration_ms=1,
        size_bytes=1,
        mtime_ns=1,
        audio_streams=(),
    )


class RecordingPrompts:
    def __init__(self, answers: list[str | None]) -> None:
        self.answers = iter(answers)
        self.calls: list[tuple[str, str | None, int, int]] = []
        self.notices: list[str] = []

    def map_episode(self, source, suggested_key, *, position, total):
        self.calls.append((source.path.name, suggested_key, position, total))
        return next(self.answers)

    def notice(self, message: str) -> None:
        self.notices.append(message)


def test_unique_automatic_mappings_do_not_prompt() -> None:
    prompts = RecordingPrompts([])

    mappings = resolve_episode_keys(
        (
            _probe("[Group] Show - 01 (1080p).mkv"),
            _probe("[Group] Show - 02 (1080p).mkv"),
        ),
        prompts,
    )

    assert [(source.path.name, key) for source, key in mappings] == [
        ("[Group] Show - 01 (1080p).mkv", "1"),
        ("[Group] Show - 02 (1080p).mkv", "2"),
    ]
    assert prompts.calls == []


def test_collisions_prompt_one_at_a_time_with_breadcrumb_positions() -> None:
    prompts = RecordingPrompts(["2", "3"])

    mappings = resolve_episode_keys(
        (
            _probe("[Group] Show - Special Alpha (1080p).mkv"),
            _probe("[Group] Show - Special Beta (1080p).mkv"),
        ),
        prompts,
    )

    assert [key for _, key in mappings] == ["2", "3"]
    assert prompts.calls == [
        ("[Group] Show - Special Alpha (1080p).mkv", None, 1, 2),
        ("[Group] Show - Special Beta (1080p).mkv", None, 2, 2),
    ]
    assert prompts.notices[0] == (
        "2 ambiguous episodes; resolving one at a time."
    )


def test_duplicate_suggestions_are_both_ambiguous_and_cannot_collide() -> None:
    prompts = RecordingPrompts(["1", "1", "2"])

    mappings = resolve_episode_keys(
        (
            _probe("[A] Show - 01 (1080p).mkv"),
            _probe("[B] Show - 01 (1080p).mkv"),
        ),
        prompts,
    )

    assert [key for _, key in mappings] == ["1", "2"]
    assert [call[2:] for call in prompts.calls] == [(1, 2), (2, 2), (2, 2)]
    assert "already assigned" in prompts.notices[-1]
