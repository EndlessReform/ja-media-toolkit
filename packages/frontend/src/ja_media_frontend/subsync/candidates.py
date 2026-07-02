from __future__ import annotations

from rich.table import Table
from rich.text import Text

from ja_media_core.subtitle_lid import SubtitleLanguage
from ja_media_core.subsync import subtitle_anchor_fit_score
from ja_media_frontend.subsync.service import SubtitleTrack


def render_candidate_table(
    *,
    tracks: list[SubtitleTrack],
    cue_indices: list[int],
    track_index: int,
    ground_truth_track: SubtitleTrack | None,
) -> Table:
    """Render the subtitle candidate summary table."""

    table = Table(
        expand=True,
        box=None,
        show_edge=False,
        pad_edge=False,
        padding=(0, 2),
        collapse_padding=True,
    )
    table.add_column("", width=1, no_wrap=True)
    table.add_column("candidate", ratio=1, overflow="ellipsis", no_wrap=True)
    table.add_column("cue", justify="right", no_wrap=True)
    table.add_column("offset", justify="right", no_wrap=True)
    table.add_column("fit", justify="right", no_wrap=True)
    table.add_column("active", justify="right", no_wrap=True)
    table.add_column("span", justify="right", no_wrap=True)
    if not tracks:
        table.add_row(
            Text(" ", style="dim"),
            Text("No subtitles loaded. Press F6 or launch with --fetch-subs.", style="dim"),
            Text("-"),
            Text("0ms"),
            Text("-"),
            Text("0.0s"),
            Text("0.0s"),
        )
        _add_ground_truth_row(table, ground_truth_track)
        return table

    for index, track in enumerate(tracks):
        table.add_row(
            Text(">" if index == track_index else " ", style="bold yellow" if index == track_index else "dim"),
            _candidate_label(track, selected=index == track_index),
            Text(_cue_label(cue_indices[index], track), style="bold" if index == track_index else ""),
            Text(
                track.timing_offset_label,
                style="bold magenta" if index == track_index and track.timing_offset_s else "dim",
            ),
            Text(anchor_fit_label(ground_truth_track, track), style="dim"),
            Text(format_duration(track.active_s)),
            Text(format_duration(track.end_s)),
        )
    _add_ground_truth_row(table, ground_truth_track)
    return table


def anchor_fit_label(
    ground_truth_track: SubtitleTrack | None,
    track: SubtitleTrack,
) -> str:
    """Return a compact timing-anchor score label for one candidate."""

    if ground_truth_track is None:
        return ""
    score = subtitle_anchor_fit_score(ground_truth_track.cues, track.cues)
    return f"{max(0.0, score):.2f}"


def _add_ground_truth_row(
    table: Table,
    ground_truth_track: SubtitleTrack | None,
) -> None:
    if ground_truth_track is None:
        return
    table.add_row(
        Text("·", style="dim"),
        Text(f"truth {ground_truth_track.label}", style="dim cyan"),
        Text("align", style="dim"),
        Text("locked", style="dim"),
        Text("-", style="dim"),
        Text(format_duration(ground_truth_track.active_s), style="dim"),
        Text(format_duration(ground_truth_track.end_s), style="dim"),
    )


def _candidate_label(track: SubtitleTrack, *, selected: bool) -> Text:
    text = Text()
    if (
        track.language_analysis is not None
        and track.language_analysis.language is SubtitleLanguage.NON_JAPANESE
    ):
        text.append("NON-JA ", style="bold red")
    text.append(track.label, style="bold cyan" if selected else "")
    return text


def _cue_label(cue_index: int, track: SubtitleTrack) -> str:
    if not track.cues:
        return "-"
    return f"{cue_index + 1}/{len(track.cues)}"


def format_duration(seconds: float) -> str:
    if seconds >= 3600:
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{int(hours)}:{int(minutes):02d}:{seconds:04.1f}"
    if seconds >= 60:
        minutes, remainder = divmod(seconds, 60)
        return f"{int(minutes)}m{remainder:04.1f}s"
    return f"{seconds:.1f}s"
