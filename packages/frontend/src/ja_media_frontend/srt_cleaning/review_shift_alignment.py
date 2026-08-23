"""Playable presentation of beginning/middle/end alignment results."""

from __future__ import annotations

from typing import Any

from rich.console import Group
from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

from ja_media_frontend.audio import MaterializedAudioPlayer
from ja_media_frontend.widgets.timeline import format_clock


POSITION_LABELS = {
    "beginning": "cue near beginning",
    "middle": "cue in middle",
    "end": "cue near end",
}


class ShiftAlignmentReviewScreen(ModalScreen[None]):
    """Play all six crop placements against the prepared episode audio."""

    CSS = """
    ShiftAlignmentReviewScreen { align: center middle; }
    #shift-dialog {
        width: 94%; height: 86%; padding: 1 2;
        background: $surface; border: tall $accent;
    }
    #shift-content { height: 1fr; padding: 1 1; }
    #shift-status { height: auto; color: $warning; }
    #shift-help { height: auto; color: $text-muted; margin-top: 1; }
    """

    def __init__(
        self,
        report: dict[str, Any],
        *,
        player: MaterializedAudioPlayer | None,
    ) -> None:
        super().__init__()
        self.report = report
        self.targets = list(report["targets"])
        self.player = player
        self.index = 0
        self.status = ""

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static(id="shift-content"),
            Static(id="shift-status"),
            Static(
                "o play original subtitle borders  |  1-6 play test versions  |  "
                "j/k next/previous cue  |  F7/Esc close",
                id="shift-help",
            ),
            id="shift-dialog",
        )

    def on_mount(self) -> None:
        self._refresh_content()

    def on_unmount(self) -> None:
        if self.player is not None:
            self.player.stop()

    def on_key(self, event) -> None:  # type: ignore[no-untyped-def]
        key = event.character or event.key
        handled = True
        if event.key in {"escape", "f7"} or key == "q":
            self.dismiss(None)
        elif key == "o":
            self._play_original()
        elif key in {"1", "2", "3", "4", "5", "6"}:
            self._play_result(int(key) - 1)
        elif key == "j":
            self._move(1)
        elif key == "k":
            self._move(-1)
        else:
            handled = False
        if handled:
            event.stop()

    @property
    def target(self) -> dict[str, Any]:
        return self.targets[self.index]

    @property
    def results(self) -> list[dict[str, Any]]:
        cue_id = str(self.target["cue_id"])
        return sorted(
            (
                row
                for row in self.report["results"]
                if str(row["target_cue_id"]) == cue_id
            ),
            key=lambda row: (
                float(row["window_size_s"]),
                ("beginning", "middle", "end").index(row["position_name"]),
            ),
        )

    def _play_original(self) -> None:
        self._play(
            float(self.target["source_start_s"]),
            float(self.target["source_end_s"]),
            "Playing original subtitle borders",
        )

    def _play_result(self, index: int) -> None:
        result = self.results[index]
        timing = result["target_result"]
        self._play(
            float(timing["aligned_start_s"]),
            float(timing["aligned_end_s"]),
            (
                f"Playing {int(result['window_size_s'])}s / "
                f"{POSITION_LABELS[result['position_name']]}"
            ),
        )

    def _play(self, start_s: float, end_s: float, status: str) -> None:
        if self.player is None:
            self.status = "Audio is not loaded."
        else:
            self.player.play(max(0.0, start_s), max(0.001, end_s - start_s))
            self.status = status
        self._refresh_content()

    def _move(self, delta: int) -> None:
        if self.player is not None:
            self.player.stop()
        self.index = max(0, min(len(self.targets) - 1, self.index + delta))
        self.status = ""
        self._refresh_content()

    def _refresh_content(self) -> None:
        if not self.is_mounted:
            return
        self.query_one("#shift-content", Static).update(
            render_shift_target(
                self.target,
                self.results,
                index=self.index,
                total=len(self.targets),
            )
        )
        self.query_one("#shift-status", Static).update(self.status)


def render_shift_target(
    target: dict[str, Any],
    results: list[dict[str, Any]],
    *,
    index: int,
    total: int,
) -> Group:
    """Explain each playable timing in domain language."""

    heading = Text(
        f"Crop-edge timing check  {index + 1}/{total}  —  {_cohort(target['cohort'])}",
        style="bold cyan",
    )
    original = Text.assemble(
        ("Original subtitle borders: ", "bold"),
        f"{format_clock(float(target['source_start_s']))} → "
        f"{format_clock(float(target['source_end_s']))}",
    )
    table = Table(expand=True, show_header=True, header_style="bold")
    table.add_column("Key / test version", min_width=25, ratio=2)
    table.add_column("Space plays / movement from middle", min_width=42, ratio=3)
    table.add_column("Result", min_width=23, ratio=2)
    middle = {
        float(row["window_size_s"]): row["target_result"]
        for row in results
        if row["position_name"] == "middle"
    }
    for key, row in enumerate(results, start=1):
        timing = row["target_result"]
        window_s = float(row["window_size_s"])
        reference = middle[window_s]
        if row["position_name"] == "middle":
            movement = "reference for this window length"
        else:
            start_shift = float(timing["aligned_start_s"]) - float(
                reference["aligned_start_s"]
            )
            end_shift = float(timing["aligned_end_s"]) - float(
                reference["aligned_end_s"]
            )
            movement = f"start {_delta(start_shift)}; end {_delta(end_shift)}"
        ordered = timing["status"] == "aligned"
        outside_crop = (
            float(timing["aligned_start_s"]) < float(row["crop_start_s"]) - 0.01
            or float(timing["aligned_end_s"]) > float(row["crop_end_s"]) + 0.01
        )
        result_text = "ordered" if ordered else "BROKEN order"
        if outside_crop:
            result_text += "\nOUTSIDE audio crop"
        result_text += (
            "\n"
            f"nearest crop edge "
            f"{float(timing['score_signals']['min_edge_distance_s']):.2f}s"
        )
        table.add_row(
            f"[{key}] {int(window_s)}s • {POSITION_LABELS[row['position_name']]}",
            f"{format_clock(float(timing['aligned_start_s']))} → "
            f"{format_clock(float(timing['aligned_end_s']))}\n{movement}",
            Text(
                result_text,
                style="green" if ordered and not outside_crop else "bold magenta",
            ),
        )
    definition = Text(
        "Nearest crop edge = the smallest gap between a predicted word border "
        "and the audio sent to the aligner. Ordered = predicted word timestamps "
        "move forward; it does not mean the timing is correct.",
        style="dim",
    )
    return Group(
        heading,
        Text(""),
        Text(str(target["text"]), style="bold white"),
        original,
        Text(""),
        table,
        definition,
    )


def _delta(value: float) -> str:
    if abs(value) < 0.005:
        return "unchanged"
    return f"{abs(value):.2f}s {'later' if value > 0 else 'earlier'}"


def _cohort(value: str) -> str:
    return {
        "reviewed_interior_broken": "ordinary dialogue that previously broke",
        "matched_interior_aligned": "matched ordinary-dialogue control",
        "selector_conflict": "broken winner with an ordered alternative",
    }.get(value, value.replace("_", " "))
