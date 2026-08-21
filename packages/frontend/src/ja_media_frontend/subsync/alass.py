"""Optional ALASS piecewise retiming for the subsync TUI.

ALASS remains an external convenience executable, not a frontend dependency.
The adapter writes the cues currently displayed by the TUI as chronological
temporary SRTs, then applies the result only to the selected in-memory track.
Promotion is the explicit persistence boundary; source subtitles are untouched.
"""

from __future__ import annotations

import shutil
import subprocess
import uuid
from dataclasses import replace
from pathlib import Path

from textual import work

from ja_media_core.proc import run as run_process
from ja_media_core.transcripts import SubtitleCue, format_srt, read_subtitle
from ja_media_frontend.subsync.service import SubtitleTrack


ALASS_EXECUTABLE = "alass-cli"
ALASS_SPLIT_PENALTY = "5"


class AlassRetimingError(RuntimeError):
    """Raised when the optional ALASS process cannot produce a valid result."""


def find_alass_executable() -> str | None:
    """Return the optional ALASS executable path when it is on ``PATH``."""

    return shutil.which(ALASS_EXECUTABLE)


def _chronological_srt(cues: list[SubtitleCue]) -> str:
    """Serialize current cues in the temporal order expected by an aligner."""

    return format_srt(
        sorted(cues, key=lambda cue: (cue.start_s, cue.end_s, cue.index))
    )


def retime_with_alass(
    *,
    executable: str,
    anchor_cues: list[SubtitleCue],
    candidate_cues: list[SubtitleCue],
    work_dir: Path,
) -> list[SubtitleCue]:
    """Run the Gate 1 ALASS piecewise-p5 arm on current in-memory cues."""

    if not anchor_cues:
        raise AlassRetimingError("Embedded anchor has no cues")
    if not candidate_cues:
        raise AlassRetimingError("Selected candidate has no cues")

    run_dir = work_dir / f"alass-p5-{uuid.uuid4().hex}"
    run_dir.mkdir(parents=True, exist_ok=False)
    anchor_path = run_dir / "anchor.srt"
    candidate_path = run_dir / "candidate.srt"
    output_path = run_dir / "retimed.srt"
    anchor_path.write_text(_chronological_srt(anchor_cues), encoding="utf-8")
    candidate_path.write_text(_chronological_srt(candidate_cues), encoding="utf-8")

    command = [
        executable,
        str(anchor_path),
        str(candidate_path),
        str(output_path),
        "--disable-fps-guessing",
        "--split-penalty",
        ALASS_SPLIT_PENALTY,
    ]
    try:
        completed = run_process(
            command,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AlassRetimingError(f"ALASS could not run: {exc}") from exc

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()[:300]
        suffix = f": {detail}" if detail else ""
        raise AlassRetimingError(
            f"ALASS exited with status {completed.returncode}{suffix}"
        )
    if not output_path.is_file():
        raise AlassRetimingError("ALASS reported success without an output SRT")

    try:
        output_cues = read_subtitle(output_path)
    except (OSError, ValueError) as exc:
        raise AlassRetimingError(f"ALASS output could not be parsed: {exc}") from exc
    if len(output_cues) != len(candidate_cues):
        raise AlassRetimingError(
            "ALASS changed the candidate cue count "
            f"({len(candidate_cues)} -> {len(output_cues)})"
        )
    return output_cues


class SubsyncAlassMixin:
    """Textual interaction layer for optional ALASS piecewise retiming."""

    def on_key(self, event) -> None:  # type: ignore[no-untyped-def]
        if event.character == "a":
            event.stop()
            self.start_alass_retime()

    def alass_help_label(self) -> str:
        """Show the action only when it can succeed for the current session."""

        if (
            self.tracks
            and self.ground_truth_track is not None
            and find_alass_executable() is not None
        ):
            return "  a ALASS p5"
        return ""

    def start_alass_retime(self) -> None:
        """Validate optional inputs synchronously, then dispatch ALASS."""

        if not self.tracks:
            self.notify("No subtitle candidate selected", severity="warning")
            return
        if self.ground_truth_track is None:
            self.notify("ALASS retiming needs an embedded anchor", severity="warning")
            return
        executable = find_alass_executable()
        if executable is None:
            self.notify("alass-cli is not on PATH", severity="warning")
            return

        index = self.track_index
        track = self.track
        self.notify(f"Retiming {track.label} with ALASS piecewise p5…")
        self._run_alass_retime(
            executable=executable,
            index=index,
            track=track,
            anchor_cues=self.ground_truth_track.cues,
        )

    @work(thread=True, exclusive=True, group="alass-retiming")
    def _run_alass_retime(
        self,
        *,
        executable: str,
        index: int,
        track: SubtitleTrack,
        anchor_cues: list[SubtitleCue],
    ) -> None:
        try:
            cues = retime_with_alass(
                executable=executable,
                anchor_cues=anchor_cues,
                candidate_cues=track.cues,
                work_dir=self.download_dir,
            )
        except AlassRetimingError as exc:
            self.call_from_thread(self._report_alass_failure, str(exc))
            return
        self.call_from_thread(self._apply_alass_result, index, track, cues)

    def _report_alass_failure(self, message: str) -> None:
        self.notify(message, severity="error")

    def _apply_alass_result(
        self,
        index: int,
        original: SubtitleTrack,
        cues: list[SubtitleCue],
    ) -> None:
        if index >= len(self.tracks) or self.tracks[index] is not original:
            self.notify(
                "Candidate list changed; discarded the ALASS result",
                severity="warning",
            )
            return
        self.tracks[index] = replace(
            original,
            cues=cues,
            modified=True,
            timing_offset_s=0.0,
        )
        self.cue_indices[index] = min(self.cue_indices[index], len(cues) - 1)
        self.normalize_window()
        self.refresh_view()
        self.notify(f"Applied ALASS piecewise p5 to {original.label}")
