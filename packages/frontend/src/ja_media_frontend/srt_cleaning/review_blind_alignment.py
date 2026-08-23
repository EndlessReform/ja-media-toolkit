"""Blind playback review for paired forced-alignment window candidates."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from rich.console import Group
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

from ja_media_frontend.audio import MaterializedAudioPlayer


CHOICES = {
    "a": "A better",
    "b": "B better",
    "t": "tie",
    "n": "neither contains the complete line",
    "x": "text absent from audio",
}


def read_blind_alignment_report(path: Path) -> dict[str, Any]:
    """Load the stability artifact consumed by the F7 review window."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_name") != "ja-media.forced-alignment.stability":
        raise ValueError(f"not a forced-alignment stability artifact: {path}")
    if not payload.get("blind_pairs"):
        raise ValueError(f"stability artifact contains no blind pairs: {path}")
    return payload


class BlindAlignmentReviewScreen(ModalScreen[None]):
    """Play randomized A/B cue borders without exposing their window duration."""

    CSS = """
    BlindAlignmentReviewScreen { align: center middle; }
    #blind-dialog {
        width: 88%; height: 72%; padding: 1 2;
        background: $surface; border: tall $accent;
    }
    #blind-content { height: 1fr; padding: 1 1; }
    #blind-status { height: auto; color: $warning; }
    #blind-help { height: auto; color: $text-muted; margin-top: 1; }
    """

    def __init__(
        self,
        report: dict[str, Any],
        *,
        player: MaterializedAudioPlayer | None,
        judgments_path: Path,
    ) -> None:
        super().__init__()
        self.report = report
        self.pairs = list(report["blind_pairs"])
        self.player = player
        self.judgments_path = judgments_path
        self.index = 0
        self.status = ""
        self.saved = _latest_judgments(judgments_path)

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static(id="blind-content"),
            Static(id="blind-status"),
            Static(
                "1 play A  2 play B  |  a A better  b B better  t tie  "
                "n neither complete  x text absent  |  j/k next/previous  "
                "F7/Esc close",
                id="blind-help",
            ),
            id="blind-dialog",
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
        elif key == "1":
            self._play("A")
        elif key == "2":
            self._play("B")
        elif key in CHOICES:
            self._save_choice(key)
        elif key == "j":
            self._move(1)
        elif key == "k":
            self._move(-1)
        else:
            handled = False
        if handled:
            event.stop()

    @property
    def pair(self) -> dict[str, Any]:
        return self.pairs[self.index]

    def _play(self, label: str) -> None:
        if self.player is None:
            self.status = "Audio is not loaded."
        else:
            candidate = self.pair["candidates"][label]
            start_s = max(0.0, float(candidate["start_s"]))
            duration_s = max(0.001, float(candidate["end_s"]) - start_s)
            self.player.play(start_s, duration_s)
            self.status = f"Playing candidate {label}"
        self._refresh_content()

    def _save_choice(self, key: str) -> None:
        row = {
            "schema_name": "ja-media.forced-alignment.blind-judgment",
            "schema_version": "1.0.0",
            "case": self.report["case"],
            "source_sha256": self.report["source_sha256"],
            "target_cue_id": self.pair["target_cue_id"],
            "source_index": self.pair["source_index"],
            "choice": CHOICES[key],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self.judgments_path.parent.mkdir(parents=True, exist_ok=True)
        with self.judgments_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        self.saved[str(self.pair["target_cue_id"])] = row
        self.status = f"Saved: {CHOICES[key]}"
        if self.index < len(self.pairs) - 1:
            self.index += 1
        self._refresh_content()

    def _move(self, delta: int) -> None:
        if self.player is not None:
            self.player.stop()
        self.index = max(0, min(len(self.pairs) - 1, self.index + delta))
        self.status = ""
        self._refresh_content()

    def _refresh_content(self) -> None:
        if not self.is_mounted:
            return
        saved = self.saved.get(str(self.pair["target_cue_id"]))
        self.query_one("#blind-content", Static).update(
            render_blind_pair(
                self.pair,
                index=self.index,
                total=len(self.pairs),
                saved_choice=saved["choice"] if saved else None,
            )
        )
        self.query_one("#blind-status", Static).update(self.status)


class BlindAlignmentReviewMixin:
    """Open the stability artifact as an F7 blind review window."""

    def action_show_alignment_ab(self) -> None:
        path = self.alignment_eval_path
        if path is None or not path.is_file():
            self.notify(
                "No stability results found for this alignment case", severity="warning"
            )
            return
        try:
            report = read_blind_alignment_report(path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self.notify(str(exc), severity="error")
            return
        self.stop_playback()
        self.push_screen(
            BlindAlignmentReviewScreen(
                report,
                player=self._player,
                judgments_path=path.parent / "blind-judgments.jsonl",
            )
        )


def _latest_judgments(path: Path) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        return {}
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return {str(row["target_cue_id"]): row for row in rows}


def render_blind_pair(
    pair: dict[str, Any], *, index: int, total: int, saved_choice: str | None
) -> Group:
    """Render only the text and opaque candidate labels shown to the reviewer."""

    heading = Text(f"Blind alignment review  {index + 1}/{total}", style="bold cyan")
    if saved_choice:
        heading.append(f"  saved: {saved_choice}", style="green")
    candidates = Text.assemble(
        ("Candidate A", "bold yellow"),
        "  press 1 to play\n",
        ("Candidate B", "bold magenta"),
        "  press 2 to play",
    )
    return Group(
        heading,
        Text(""),
        Text(str(pair["text"]), style="bold white"),
        Text(""),
        candidates,
    )
