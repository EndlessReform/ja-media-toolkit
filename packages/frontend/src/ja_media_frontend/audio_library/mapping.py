"""Episode-key suggestion triage for the interactive ingest wizard."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from typing import Protocol

from ja_media_core.audio_library import SourceMediaProbe
from ja_media_frontend.audio_library.discovery import suggest_episode_key
from ja_media_frontend.audio_library.materialize import artifact_filename


class EpisodeMappingPrompts(Protocol):
    """The prompt surface needed to resolve ambiguous episode identities."""

    def map_episode(
        self,
        source: SourceMediaProbe,
        suggested_key: str | None,
        *,
        position: int,
        total: int,
    ) -> str | None: ...

    def notice(self, message: str) -> None: ...


def resolve_episode_keys(
    probes: Sequence[SourceMediaProbe],
    prompts: EpisodeMappingPrompts,
) -> tuple[tuple[SourceMediaProbe, str], ...]:
    """Accept unique suggestions and resolve only ambiguous files interactively.

    Missing suggestions and duplicate suggested keys are ambiguous. They are
    handled one at a time, and a manually chosen key cannot collide with an
    already accepted key.
    """

    suggestions = tuple(
        (source, suggest_episode_key(source.path)) for source in probes
    )
    counts = Counter(key for _, key in suggestions if key is not None)
    accepted = [
        (source, key)
        for source, key in suggestions
        if key is not None and counts[key] == 1
    ]
    ambiguous = [
        (source, key)
        for source, key in suggestions
        if key is None or counts[key] > 1
    ]
    used_keys = {key for _, key in accepted}

    if ambiguous:
        prompts.notice(f"{len(ambiguous)} ambiguous episodes; resolving one at a time.")
    for position, (source, suggestion) in enumerate(ambiguous, 1):
        while True:
            key = prompts.map_episode(
                source,
                suggestion,
                position=position,
                total=len(ambiguous),
            )
            if key is None:
                break
            try:
                artifact_filename(key)
            except ValueError as error:
                prompts.notice(str(error))
                continue
            if key in used_keys:
                prompts.notice(
                    f"Episode key {key!r} is already assigned; choose another key."
                )
                continue
            used_keys.add(key)
            accepted.append((source, key))
            break

    return tuple(sorted(accepted, key=lambda item: int(item[1])))
