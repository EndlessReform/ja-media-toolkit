"""Rich terminal prompts for the Phase 1 ingest wizard."""

from __future__ import annotations

from collections.abc import Sequence

from rich.console import Console
from rich.table import Table

from ja_media_core.anilist_search import SearchResult
from ja_media_core.audio_library import (
    AnimeAudioSeriesMetadata,
    AudioStreamProbe,
    MaterializationPlan,
    SourceMediaProbe,
)
from ja_media_core.media_filename import parse_media_filename
from ja_media_frontend.audio_library.materialize import artifact_filename


class ConsoleWizardPrompts:
    """Human-in-the-loop decisions rendered with Rich and plain input."""

    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console()

    def choose_anime(
        self, query: str, candidates: Sequence[SearchResult]
    ) -> int | str | None:
        self.console.print(f"\n[bold]AniList matches for[/bold] {query!r}")
        table = Table(show_header=True)
        for heading in ("#", "ID", "English", "Native", "Romaji", "Season", "Format", "Score"):
            table.add_column(heading)
        for index, item in enumerate(candidates, 1):
            table.add_row(
                str(index),
                str(item.anilist_id or ""),
                item.title_english or "",
                item.title_native or "",
                item.title_romaji or "",
                " ".join(part for part in (item.season, str(item.season_year or "")) if part),
                item.format or "",
                f"{item.score:.2f}",
            )
        self.console.print(table)
        while True:
            answer = input(
                "Choose #, enter 'id 154587', 'search title', or 'q': "
            ).strip()
            if answer.casefold() in {"q", "quit", ""}:
                return None
            if answer.casefold().startswith("id "):
                value = answer[3:].strip()
                if value.isdecimal() and int(value) > 0:
                    return int(value)
            if answer.casefold().startswith("search "):
                query = answer[7:].strip()
                if query:
                    return query
            if answer.isdecimal() and 1 <= int(answer) <= len(candidates):
                selected = candidates[int(answer) - 1].anilist_id
                if selected is not None:
                    return selected
            self.console.print("[red]Enter a listed number, explicit ID, search, or q.[/red]")

    def confirm_series(self, metadata: AnimeAudioSeriesMetadata) -> bool:
        self.console.print()
        self.console.print(f"[bold]{metadata.title_preferred}[/bold]")
        self.console.print(
            f"AniList {metadata.anilist_id} · {metadata.format or '?'} · "
            f"{metadata.season or '?'} {metadata.season_year or ''} · "
            f"{metadata.episode_count or '?'} episodes"
        )
        self.console.print(
            f"Native: {metadata.title_native or '—'}\n"
            f"Romaji: {metadata.title_romaji or '—'}"
        )
        return self._confirm("Use this series?")

    def map_episode(
        self,
        source: SourceMediaProbe,
        suggested_key: str | None,
        *,
        position: int,
        total: int,
    ) -> str | None:
        parsed = parse_media_filename(source.path.stem)
        stream_summary = ", ".join(
            f"{item.audio_ordinal}:{item.codec}/{item.language or '?'}"
            for item in source.audio_streams
        ) or "none"
        self.console.print(
            f"\n[bold cyan]{total} ambiguous episodes, doing "
            f"{position}/{total}[/bold cyan]\n"
            f"[bold]{source.path.name}[/bold]\n"
            f"  parsed title: {parsed.title or '—'}\n"
            f"  parsed episode: {', '.join(map(str, parsed.episode_values)) or '—'}\n"
            f"  duration: {source.duration_ms / 60_000:.1f} min\n"
            f"  audio: {stream_summary}"
        )
        default = suggested_key or ""
        answer = input(
            f"Episode key [{default or 'required'}], 'skip', or 'q': "
        ).strip()
        if answer.casefold() == "q":
            raise KeyboardInterrupt
        if answer.casefold() in {"skip", "s"}:
            return None
        return answer or suggested_key

    def choose_audio_stream(self, source: SourceMediaProbe) -> AudioStreamProbe | None:
        self.console.print(f"Choose audio stream for {source.path.name}:")
        for item in source.audio_streams:
            self.console.print(
                f"  {item.audio_ordinal}: global={item.global_index} "
                f"{item.codec} {item.language or '?'} "
                f"{item.channels or '?'}ch {item.sample_rate_hz or '?'}Hz "
                f"{'[default]' if item.default else ''}"
            )
        while True:
            answer = input("Audio ordinal or 'skip': ").strip()
            if answer.casefold() in {"skip", "s", ""}:
                return None
            if answer.isdecimal():
                for item in source.audio_streams:
                    if item.audio_ordinal == int(answer):
                        return item
            self.console.print("[red]Choose a listed audio ordinal or skip.[/red]")

    def confirm_plan(self, plan: MaterializationPlan) -> bool:
        self.console.print(
            f"\n[bold]Execution plan[/bold]\n"
            f"Destination: {plan.series_directory}\n"
            f"Profile: {plan.profile.name}"
        )
        table = Table("Episode", "Source", "Stream", "Output")
        for mapping in plan.mappings:
            table.add_row(
                mapping.episode_key,
                mapping.source_path.name,
                f"{mapping.stream.audio_ordinal} (global {mapping.stream.global_index})",
                artifact_filename(mapping.episode_key),
            )
        self.console.print(table)
        return self._confirm("Materialize this plan?")

    def notice(self, message: str) -> None:
        self.console.print(message)

    @staticmethod
    def _confirm(question: str) -> bool:
        return input(f"{question} [y/N] ").strip().casefold() in {"y", "yes"}
