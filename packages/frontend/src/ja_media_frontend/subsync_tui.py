from __future__ import annotations

import glob
import importlib
import os
import re
import shutil
import subprocess
import tempfile
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Literal

from dotenv import load_dotenv
from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Label,
    OptionList,
    Select,
    Static,
)

import PTN

from ja_media_core.kitsunekko import (
    HttpKitsunekkoSubtitlesClient,
    KitsunekkoFileListResponse,
)
from ja_media_core.srt import SubtitleCue, format_srt, read_srt


SPAN_STYLES = [
    "green",
    "bright_green",
    "cyan",
    "bright_cyan",
    "magenta",
    "bright_magenta",
    "blue",
    "bright_blue",
]
ACTIVE_SPAN_STYLE = "bold black on yellow"
GAP_STYLE = "dim"
SPAN_BLOCK = "▀"
GAP_BLOCK = " "
RemoteSourceKind = Literal["anilist", "tvdb"]
PCM_SAMPLE_RATE = 48_000
PCM_CHANNELS = 1
PCM_SAMPLE_WIDTH_BYTES = 2


def _same_file(left: Path, right: Path) -> bool:
    """Return whether two paths identify the same file, tolerating missing paths."""

    try:
        return left.samefile(right)
    except OSError:
        return False


def _copy_file_contents_atomic(source: Path, destination: Path) -> None:
    """Write a byte-for-byte copy without preserving source metadata.

    `shutil.copy2()` is tempting here, but it preserves mode and timestamps after
    the content copy. Some NFS mounts allow normal writes while rejecting those
    metadata updates, which makes a successful promotion look like a permission
    failure. A sidecar SRT only needs the bytes, so write a temporary file in the
    destination directory and atomically replace the sidecar.
    """

    tmp_path: Path | None = None
    try:
        for _ in range(100):
            candidate = destination.with_name(
                f".{destination.name}.{uuid.uuid4().hex}.tmp"
            )
            try:
                fd = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
            except FileExistsError:
                continue
            tmp_path = candidate
            break
        else:  # pragma: no cover - UUID collisions are not realistically reachable.
            raise FileExistsError(f"could not allocate temp file for {destination}")

        with os.fdopen(fd, "wb") as output_file, source.open("rb") as input_file:
            shutil.copyfileobj(input_file, output_file)
            output_file.flush()
            os.fsync(output_file.fileno())

        os.replace(tmp_path, destination)
        tmp_path = None
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


@dataclass(frozen=True)
class SubtitleTrack:
    """One subtitle candidate loaded for first-pass timing review."""

    path: Path
    cues: list[SubtitleCue]
    repo_path: str | None = None
    subtitle_id: str | None = None

    @property
    def label(self) -> str:
        """Human-readable candidate label for local and fetched tracks."""

        return self.repo_path or self.path.name

    @property
    def end_s(self) -> float:
        return max((cue.end_s for cue in self.cues), default=0.0)

    @property
    def active_s(self) -> float:
        return sum(max(0.0, cue.end_s - cue.start_s) for cue in self.cues)


@dataclass
class RemoteLookupState:
    """Current Kitsunekko lookup selector visible in the TUI."""

    source: RemoteSourceKind | None = None
    external_id: int | None = None
    episode_number: int | None = None
    media_kind: str | None = "tv"
    status: str = ""


@dataclass(frozen=True)
class RemoteLookupRequest:
    """Values submitted from the in-flight Kitsunekko lookup modal."""

    source: RemoteSourceKind
    external_id: int
    episode_number: int
    media_kind: str | None = "tv"


@dataclass(frozen=True)
class ManualSubtitlePickRequest:
    """Remote subtitle row chosen from the full series inventory."""

    file: dict[str, Any]


@dataclass(frozen=True)
class PcmAudioSource:
    """Decoded audio held in RAM for deterministic subtitle cue playback.

    The TUI repeatedly auditions very short cue ranges. Owning decoded PCM means
    each cue becomes a byte slice, so playback no longer depends on container
    seeking, decoder preroll, or killing a fresh player process per keypress.
    """

    source_path: Path
    sample_rate: int
    channels: int
    sample_width_bytes: int
    pcm: bytes

    @property
    def bytes_per_frame(self) -> int:
        return self.channels * self.sample_width_bytes

    @property
    def frame_count(self) -> int:
        return len(self.pcm) // self.bytes_per_frame

    @property
    def duration_s(self) -> float:
        return self.frame_count / self.sample_rate

    def slice_bytes(self, start_s: float, duration_s: float) -> bytes:
        """Return the exact PCM frame range for a subtitle cue."""

        start_frame = max(0, round(start_s * self.sample_rate))
        requested_frames = max(1, round(duration_s * self.sample_rate))
        end_frame = min(self.frame_count, start_frame + requested_frames)
        if end_frame <= start_frame:
            return b""
        start_byte = start_frame * self.bytes_per_frame
        end_byte = end_frame * self.bytes_per_frame
        return self.pcm[start_byte:end_byte]


class PcmSlicePlayer:
    """Play in-memory PCM slices through the system's default audio device."""

    def __init__(self, audio: PcmAudioSource) -> None:
        self.audio = audio
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._stream: Any | None = None

    def play(self, start_s: float, duration_s: float) -> None:
        """Stop any active slice and start playing the requested cue bytes."""

        self.stop()
        data = self.audio.slice_bytes(start_s, duration_s)
        if not data:
            return
        sounddevice = _load_sounddevice()
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._play_bytes,
            args=(sounddevice, data, self._stop_event),
            name="subsync-pcm-playback",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Abort queued output and wait briefly for the playback worker to exit."""

        thread = self._thread
        self._thread = None
        self._stop_event.set()
        with self._lock:
            stream = self._stream
        if stream is not None:
            try:
                stream.abort()
            except Exception:
                pass
        if thread is not None and thread.is_alive():
            thread.join(timeout=0.5)

    def is_playing(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def _play_bytes(
        self,
        sounddevice: ModuleType,
        data: bytes,
        stop_event: threading.Event,
    ) -> None:
        chunk_size = self.audio.bytes_per_frame * 2048
        try:
            with sounddevice.RawOutputStream(
                samplerate=self.audio.sample_rate,
                channels=self.audio.channels,
                dtype="int16",
                blocksize=2048,
            ) as stream:
                with self._lock:
                    self._stream = stream
                for offset in range(0, len(data), chunk_size):
                    if stop_event.is_set():
                        break
                    stream.write(data[offset : offset + chunk_size])
        finally:
            with self._lock:
                self._stream = None


def run_subsync_tui(
    *,
    source_path: str,
    srt_inputs: list[str],
    window_s: float,
    anilist_id: int | None = None,
    tvdb_id: int | None = None,
    episode_number: int | None = None,
    fetch_subs: bool = False,
    tvdb_media_kind: str | None = "tv",
) -> None:
    """Load subtitle candidates and run the Textual subsync shell."""

    load_dotenv()

    source = Path(source_path).expanduser().resolve()
    if not source.is_file():
        raise SystemExit(f"subsync source is not a file: {source}")
    if window_s <= 0:
        raise SystemExit("--window-s must be positive")
    if anilist_id is not None and tvdb_id is not None:
        raise SystemExit("Pass only one of --anilist or --tvdb")

    remote_state = RemoteLookupState(
        source=(
            "anilist"
            if anilist_id is not None
            else "tvdb"
            if tvdb_id is not None
            else None
        ),
        external_id=anilist_id if anilist_id is not None else tvdb_id,
        episode_number=episode_number or runtime_episode_number(source.stem),
        media_kind=tvdb_media_kind,
    )

    # (a) Explicit SRT inputs — always first, in CLI order.
    srt_paths = resolve_srt_inputs(srt_inputs, allow_empty=True)
    tracks = []
    for path in srt_paths:
        try:
            tracks.append(SubtitleTrack(path=path, cues=read_srt(path)))
        except ValueError as exc:
            raise SystemExit(f"Could not parse {path}: {exc}") from exc

    with tempfile.TemporaryDirectory(prefix="ja-media-subsync-") as tmpdir:
        session_dir = Path(tmpdir)
        playback_source = load_pcm_audio_source(source)
        app = SubsyncTuiApp(
            audio_source=playback_source,
            tracks=tracks,
            initial_window_s=window_s,
            remote_state=remote_state,
            download_dir=session_dir,
        )

        # (c) Remote tracks — inserted between explicit and sidecar.
        if fetch_subs:
            app.fetch_remote_tracks_or_exit()

        # (b) Existing sidecar — always appended at the back so the user can
        # compare their pick against what's already on disk. Skip if already
        # loaded via explicit input or remote fetch.
        _loaded_paths = {t.path for t in app.tracks}
        _sidecar = source.with_suffix(".srt")
        if _sidecar.is_file() and _sidecar not in _loaded_paths:
            try:
                app.tracks.append(SubtitleTrack(path=_sidecar, cues=read_srt(_sidecar)))
                app.cue_indices.append(0)
            except ValueError as exc:
                raise SystemExit(f"Could not parse sidecar {_sidecar}: {exc}") from exc

        app.run()


def resolve_srt_inputs(inputs: list[str], *, allow_empty: bool = False) -> list[Path]:
    """Resolve exact SRT paths and quoted glob patterns into unique files."""

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
                raise SystemExit(f"No SRT files matched pattern: {raw_input}")
        else:
            matches = [Path(expanded).resolve()]

        for path in matches:
            if path in seen:
                continue
            if not path.is_file():
                raise SystemExit(f"SRT input is not a file: {path}")
            if path.suffix.lower() != ".srt":
                raise SystemExit(f"SRT input does not end in .srt: {path}")
            seen.add(path)
            paths.append(path)

    if not paths and not allow_empty:
        raise SystemExit("No SRT candidates were provided")
    return paths


def load_pcm_audio_source(source: Path) -> PcmAudioSource:
    """Decode the first audio stream into review-grade PCM held in memory.

    This intentionally pays the decode cost once at TUI startup. For a typical
    24-minute episode, mono 48 kHz signed 16-bit PCM is roughly 138 MB, which is
    a worthwhile trade for exact cue slicing and local playback even when the
    source media lives on flaky network storage.
    """

    if shutil.which("ffmpeg") is None:
        raise SystemExit("ffmpeg not found; cannot decode source audio")

    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-map",
        "0:a:0",
        "-vn",
        "-ac",
        str(PCM_CHANNELS),
        "-ar",
        str(PCM_SAMPLE_RATE),
        "-c:a",
        "pcm_s16le",
        "-f",
        "s16le",
        "pipe:1",
    ]
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        detail = detail or "ffmpeg produced no diagnostic output"
        raise SystemExit(f"Could not decode first audio stream with ffmpeg:\n{detail}")
    if not result.stdout:
        raise SystemExit(f"ffmpeg decoded no audio from source: {source}")
    return PcmAudioSource(
        source_path=source,
        sample_rate=PCM_SAMPLE_RATE,
        channels=PCM_CHANNELS,
        sample_width_bytes=PCM_SAMPLE_WIDTH_BYTES,
        pcm=result.stdout,
    )


def _load_sounddevice() -> ModuleType:
    try:
        return importlib.import_module("sounddevice")
    except ImportError as exc:
        raise RuntimeError("sounddevice not installed; cannot play PCM audio") from exc


def runtime_episode_number(filename_stem: str) -> int | None:
    """Parse a local episode number from a media filename stem with PTN."""

    parsed = PTN.parse(filename_stem)
    return _first_positive_int(parsed.get("episode"))


def _first_positive_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, float):
        return int(value) if value.is_integer() and value > 0 else None
    if isinstance(value, str):
        cleaned = value.strip()
        if cleaned.isdecimal():
            parsed = int(cleaned)
            return parsed if parsed > 0 else None
        for separator in ("-", "~", "_", " "):
            if separator in cleaned:
                for part in cleaned.split(separator):
                    parsed = _first_positive_int(part)
                    if parsed is not None:
                        return parsed
        return None
    if isinstance(value, (list, tuple, set)):
        for item in value:
            parsed = _first_positive_int(item)
            if parsed is not None:
                return parsed
    return None


class ConfirmOverwriteModal(ModalScreen[bool]):
    """Ask the user whether to overwrite an existing sidecar SRT."""

    CSS = """
    ConfirmOverwriteModal {
        align: center middle;
    }

    #confirm-dialog {
        width: 64;
        height: auto;
        padding: 1 2;
        background: $surface;
        border: tall $accent;
    }

    #confirm-path {
        height: auto;
        margin-top: 1;
        color: $text-muted;
    }

    #confirm-actions {
        height: auto;
        margin-top: 1;
    }
    """

    def __init__(self, destination: Path) -> None:
        super().__init__()
        self.destination = destination

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label("Overwrite existing subtitle?"),
            Static(str(self.destination), id="confirm-path"),
            Horizontal(
                Button("No", id="confirm-no"),
                Button("Yes", variant="primary", id="confirm-yes"),
                id="confirm-actions",
            ),
            id="confirm-dialog",
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "confirm-no":
            self.dismiss(False)
        elif event.button.id == "confirm-yes":
            self.dismiss(True)


class RemoteLookupModal(ModalScreen[RemoteLookupRequest | None]):
    """Textual modal for changing the Kitsunekko selector while the TUI runs."""

    CSS = """
    RemoteLookupModal {
        align: center middle;
    }

    #remote-dialog {
        width: 64;
        height: auto;
        padding: 1 2;
        background: $surface;
        border: tall $accent;
    }

    #remote-dialog Label {
        margin-top: 1;
    }

    #remote-actions {
        height: auto;
        margin-top: 1;
    }
    """

    def __init__(self, state: RemoteLookupState) -> None:
        super().__init__()
        self.state = state

    def compose(self) -> ComposeResult:
        source = self.state.source or "anilist"
        yield Vertical(
            Label("Kitsunekko lookup"),
            Label("Source"),
            Select(
                [("AniList", "anilist"), ("TVDB", "tvdb")],
                value=source,
                allow_blank=False,
                id="remote-source",
            ),
            Label("Numeric ID"),
            Input(value=str(self.state.external_id or ""), id="remote-id"),
            Label("Episode"),
            Input(value=str(self.state.episode_number or ""), id="remote-episode"),
            Label("TVDB media kind"),
            Input(value=self.state.media_kind or "tv", id="remote-kind"),
            Horizontal(
                Button("Fetch", variant="primary", id="remote-fetch"),
                Button("Cancel", id="remote-cancel"),
                id="remote-actions",
            ),
            id="remote-dialog",
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "remote-cancel":
            self.dismiss(None)
            return
        if event.button.id != "remote-fetch":
            return
        request = self._request_from_inputs()
        if request is not None:
            self.dismiss(request)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        request = self._request_from_inputs()
        if request is not None:
            self.dismiss(request)

    def _request_from_inputs(self) -> RemoteLookupRequest | None:
        source_value = self.query_one("#remote-source", Select).value
        source: RemoteSourceKind = "tvdb" if source_value == "tvdb" else "anilist"
        external_id = self._positive_input("#remote-id", "ID")
        episode_number = self._positive_input("#remote-episode", "episode")
        if external_id is None or episode_number is None:
            return None
        media_kind = self.query_one("#remote-kind", Input).value.strip() or "tv"
        return RemoteLookupRequest(
            source=source,
            external_id=external_id,
            episode_number=episode_number,
            media_kind=media_kind,
        )

    def _positive_input(self, selector: str, label: str) -> int | None:
        raw_value = self.query_one(selector, Input).value.strip()
        if not raw_value.isdecimal() or int(raw_value) <= 0:
            self.notify(f"{label} must be a positive integer", severity="error")
            return None
        return int(raw_value)


class RemoteFilePickModal(ModalScreen[ManualSubtitlePickRequest | None]):
    """Typeahead modal for choosing one subtitle from a series inventory."""

    CSS = """
    RemoteFilePickModal {
        align: center middle;
    }

    #remote-pick-dialog {
        width: 96;
        height: 30;
        padding: 1 2;
        background: $surface;
        border: tall $accent;
    }

    #remote-pick-message {
        height: auto;
        color: $text-muted;
    }

    #remote-pick-filter {
        margin-top: 1;
        margin-bottom: 1;
    }

    #remote-pick-list {
        height: 1fr;
    }

    #remote-pick-actions {
        height: auto;
        margin-top: 1;
    }
    """

    MAX_OPTIONS = 25

    def __init__(self, files: tuple[dict[str, Any], ...], *, message: str) -> None:
        super().__init__()
        self.files = files
        self.message = message
        self.filtered_files: list[dict[str, Any]] = []

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label("Choose Kitsunekko subtitle"),
            Static(self.message, id="remote-pick-message"),
            Input(
                placeholder="Filter by filename, group, episode, tags...",
                id="remote-pick-filter",
            ),
            OptionList(id="remote-pick-list"),
            Horizontal(
                Button("Use selected", variant="primary", id="remote-pick-use"),
                Button("Cancel", id="remote-pick-cancel"),
                id="remote-pick-actions",
            ),
            id="remote-pick-dialog",
        )

    def on_mount(self) -> None:
        self._refresh_options("")
        self.query_one("#remote-pick-filter", Input).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "remote-pick-filter":
            self._refresh_options(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "remote-pick-filter":
            self._dismiss_selected()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id == "remote-pick-list":
            self._dismiss_index(event.index)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "remote-pick-cancel":
            self.dismiss(None)
        elif event.button.id == "remote-pick-use":
            self._dismiss_selected()

    def _refresh_options(self, query: str) -> None:
        if query.strip():
            matches = sorted(
                self.files,
                key=lambda file: _remote_file_match_sort_key(file, query),
            )
        else:
            matches = list(self.files)
        self.filtered_files = matches[: self.MAX_OPTIONS]
        option_list = self.query_one("#remote-pick-list", OptionList)
        option_list.set_options(
            [_remote_file_option_label(file) for file in self.filtered_files]
        )
        if self.filtered_files:
            option_list.highlighted = 0

    def _dismiss_selected(self) -> None:
        option_list = self.query_one("#remote-pick-list", OptionList)
        highlighted = option_list.highlighted
        if highlighted is None:
            self.notify("No subtitle selected", severity="error")
            return
        self._dismiss_index(highlighted)

    def _dismiss_index(self, index: int) -> None:
        if index < 0 or index >= len(self.filtered_files):
            self.notify("No subtitle selected", severity="error")
            return
        self.dismiss(ManualSubtitlePickRequest(self.filtered_files[index]))


class SubsyncTuiApp(App[None]):
    """A first-pass Textual shell for inspecting subtitle timing activity."""

    BINDINGS = [
        ("f6", "open_remote_lookup", "Kitsunekko"),
        ("f7", "open_remote_file_picker", "Pick subtitle"),
    ]

    CSS = """
    Screen {
        layout: vertical;
    }

    #source {
        height: auto;
        padding: 0 1;
        background: $surface;
    }

    #candidates {
        height: auto;
        max-height: 10;
        padding: 0 1;
    }

    #timeline {
        height: auto;
        padding: 0 1;
    }

    #active {
        height: 1fr;
        min-height: 8;
        padding: 0 1;
    }

    #help {
        height: auto;
        padding: 0 1;
        background: $surface;
        color: $text-muted;
    }
    """

    TITLE = "ja-media subsync"

    def __init__(
        self,
        *,
        audio_source: PcmAudioSource,
        tracks: list[SubtitleTrack],
        initial_window_s: float,
        remote_state: RemoteLookupState | None = None,
        download_dir: Path | None = None,
    ) -> None:
        super().__init__()
        self.audio_source = audio_source
        self.tracks = tracks
        self.track_index = 0
        self.cue_indices = [0 for _ in tracks]
        self.window_start_s = 0.0
        self.window_s = initial_window_s
        self.remote_state = remote_state or RemoteLookupState()
        self.download_dir = download_dir or Path(
            tempfile.mkdtemp(prefix="ja-media-subsync-")
        )
        self._pending_remote_file_picker_message = ""
        self._pending_g = False
        self._player = PcmSlicePlayer(audio_source)
        self._playback_status = ""

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static(id="source")
        yield Static(id="candidates")
        yield Static(id="timeline")
        yield Static(id="active")
        yield Static(id="help")
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_view()
        if self._pending_remote_file_picker_message:
            message = self._pending_remote_file_picker_message
            self._pending_remote_file_picker_message = ""
            self.call_after_refresh(self._open_remote_file_picker, message)

    def on_resize(self, event) -> None:  # type: ignore[no-untyped-def]
        if self.is_mounted:
            self.refresh_view()

    def on_unmount(self) -> None:
        self.stop_playback()

    def on_key(self, event) -> None:  # type: ignore[no-untyped-def]
        key = event.key
        char = event.character
        handled = True
        if char == "q":
            self.stop_playback()
            self.exit()
        elif key == "ctrl+c":
            self.copy_current_subtitle()
        elif key == "f6":
            self.action_open_remote_lookup()
        elif key == "f7":
            self.action_open_remote_file_picker()
        elif key == "space" or char == " ":
            self.toggle_playback()
        elif char == "h":
            self.move_cue(-1)
        elif char == "l":
            self.move_cue(1)
        elif char == "j":
            self.move_track(1)
        elif char == "k":
            self.move_track(-1)
        elif char == "g":
            if self._pending_g:
                self.go_start()
            else:
                self._pending_g = True
                self.set_timer(0.75, self._clear_g_pending)
                self.refresh_view()
        elif char == "G":
            self.go_end()
        elif key in {"ctrl+f", "page_down", "pagedown"}:
            self.page_window(1.0)
        elif key in {"ctrl+b", "page_up", "pageup"}:
            self.page_window(-1.0)
        elif key == "ctrl+d":
            self.page_window(0.5)
        elif key == "ctrl+u":
            self.page_window(-0.5)
        elif char in {"+", "="}:
            self.zoom_window(0.5)
        elif char == "p":
            self.action_promote()
        elif char in {"-", "_"}:
            self.zoom_window(2.0)
        else:
            handled = False

        if handled:
            event.stop()

    @property
    def track(self) -> SubtitleTrack:
        if not self.tracks:
            raise RuntimeError("No subtitle tracks are loaded")
        return self.tracks[self.track_index]

    @property
    def cue_index(self) -> int:
        return self.cue_indices[self.track_index]

    @property
    def current_cue(self) -> SubtitleCue | None:
        if not self.tracks:
            return None
        if not self.track.cues:
            return None
        return self.track.cues[self.cue_index]

    def move_cue(self, delta: int) -> None:
        if not self.tracks or not self.track.cues:
            return
        if self.is_playing():
            self.stop_playback()
            self._playback_status = "stopped"
        self.cue_indices[self.track_index] = max(
            0,
            min(len(self.track.cues) - 1, self.cue_index + delta),
        )
        self.ensure_cue_visible()
        self.refresh_view()

    def move_track(self, delta: int) -> None:
        if not self.tracks:
            return
        self.track_index = (self.track_index + delta) % len(self.tracks)
        self.normalize_window()
        self.select_cue_near_time(self.window_start_s + self.window_s / 2)
        self.refresh_view()

    def page_window(self, pages: float) -> None:
        self.window_start_s += pages * self.window_s
        self.normalize_window()
        self.select_cue_near_time(self.window_start_s + self.window_s / 2)
        self.refresh_view()

    def zoom_window(self, factor: float) -> None:
        center_s = self._focus_time_s()
        self.window_s = max(5.0, min(self.timeline_end_s(), self.window_s * factor))
        self.window_start_s = center_s - self.window_s / 2
        self.normalize_window()
        self.refresh_view()

    def go_start(self) -> None:
        self._pending_g = False
        self.window_start_s = 0.0
        if self.tracks and self.track.cues:
            self.cue_indices[self.track_index] = 0
        self.refresh_view()

    def go_end(self) -> None:
        self._pending_g = False
        if self.tracks and self.track.cues:
            self.cue_indices[self.track_index] = len(self.track.cues) - 1
        self.window_start_s = self.timeline_end_s() - self.window_s
        self.normalize_window()
        self.refresh_view()

    def ensure_cue_visible(self) -> None:
        if not self.tracks:
            return
        cue = self.current_cue
        if cue is None:
            return
        if cue.start_s < self.window_start_s:
            self.window_start_s = cue.start_s - self.window_s * 0.15
        elif cue.end_s > self.window_start_s + self.window_s:
            self.window_start_s = cue.end_s - self.window_s * 0.85
        self.normalize_window()

    def select_cue_near_time(self, timestamp_s: float) -> None:
        if not self.tracks:
            return
        cues = self.track.cues
        if not cues:
            self.cue_indices[self.track_index] = 0
            return
        for index, cue in enumerate(cues):
            if cue.end_s >= timestamp_s:
                self.cue_indices[self.track_index] = index
                return
        self.cue_indices[self.track_index] = len(cues) - 1

    def normalize_window(self) -> None:
        max_start_s = max(0.0, self.timeline_end_s() - self.window_s)
        self.window_start_s = max(0.0, min(max_start_s, self.window_start_s))

    def timeline_end_s(self) -> float:
        if not self.tracks:
            return self.window_s
        return max(self.track.end_s, self.window_s)

    def refresh_view(self) -> None:
        self.query_one("#source", Static).update(self.render_source())
        self.query_one("#candidates", Static).update(self.render_candidates())
        self.query_one("#timeline", Static).update(self.render_timeline())
        self.query_one("#active", Static).update(self.render_active_cue())
        self.query_one("#help", Static).update(self.render_help())

    def render_source(self) -> Text:
        text = Text()
        text.append("source: ", style="bold")
        text.append(str(self.audio_source.source_path))
        text.append("  ")
        text.append(
            (
                f"pcm {self.audio_source.sample_rate}Hz "
                f"{self.audio_source.channels}ch "
                f"{format_duration(self.audio_source.duration_s)}"
            ),
            style="dim",
        )
        remote_label = self.remote_label()
        if remote_label:
            text.append("  ")
            text.append(remote_label, style="bold cyan")
        if self.remote_state.status:
            text.append("  ")
            text.append(self.remote_state.status, style="dim")
        return text

    def render_candidates(self) -> Table:
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
        table.add_column("total", justify="right", no_wrap=True)
        table.add_column("active", justify="right", no_wrap=True)
        table.add_column("span", justify="right", no_wrap=True)
        if not self.tracks:
            table.add_row(
                Text(" ", style="dim"),
                Text(
                    "No subtitles loaded. Press F6 or launch with --fetch-subs.",
                    style="dim",
                ),
                Text("-"),
                Text("0"),
                Text("0.0s"),
                Text("0.0s"),
            )
            return table
        for index, track in enumerate(self.tracks):
            cue_label = "-"
            if track.cues:
                cue_label = f"{self.cue_indices[index] + 1}/{len(track.cues)}"
            selected = index == self.track_index
            marker_style = "bold yellow" if selected else "dim"
            candidate_style = "bold cyan" if selected else ""
            table.add_row(
                Text(">" if selected else " ", style=marker_style),
                Text(track.label, style=candidate_style),
                Text(cue_label, style="bold" if selected else ""),
                Text(str(len(track.cues))),
                Text(format_duration(track.active_s)),
                Text(format_duration(track.end_s)),
            )
        return table

    def render_timeline(self) -> Panel:
        if not self.tracks:
            return Panel(
                "Press F6 to select a Kitsunekko lookup.", title="No subtitles"
            )
        start_s = self.window_start_s
        end_s = start_s + self.window_s
        cue = self.current_cue
        bar_width = self.timeline_bar_width()
        lines = [
            Text.assemble(
                ("window: ", "bold"),
                (format_clock(start_s), "cyan"),
                " -> ",
                (format_clock(end_s), "cyan"),
                f"  ({self.window_s:.1f}s shown)",
            ),
            self._activity_bar(
                width=bar_width,
                start_s=start_s,
                end_s=end_s,
                active_cue=cue,
            ),
            self._tick_bar(width=bar_width, start_s=start_s, end_s=end_s),
        ]
        return Panel(Group(*lines), title=self.track.path.name, expand=True)

    def timeline_bar_width(self) -> int:
        """Return the usable cell width for the rendered timeline signal."""

        timeline_width = self.query_one("#timeline", Static).size.width
        screen_width = self.size.width
        available_width = timeline_width or screen_width or 96

        # Static has horizontal padding and Rich panels spend a few cells on
        # borders plus inner padding. Use the remaining space for signal.
        return max(24, available_width - 8)

    def render_active_cue(self) -> Panel:
        if not self.tracks:
            return Panel("No subtitle candidates are loaded.", title="Current subtitle")
        cue = self.current_cue
        if cue is None:
            return Panel("No cues in selected SRT.", title="Current subtitle")

        text = Text()
        text.append(
            f"{cue.index}  {format_clock(cue.start_s)} -> {format_clock(cue.end_s)}\n",
            style="bold cyan",
        )
        text.append(cue.text or "<empty cue>")
        return Panel(text, title="Current subtitle", expand=True)

    def render_help(self) -> str:
        pending = "  g..." if self._pending_g else ""
        playback_status = self.playback_status()
        playback = f"  {playback_status}" if playback_status else ""
        return (
            "space play/stop  h/l cue  j/k SRT  gg/G start/end  Ctrl-f/b page  "
            "Ctrl-d/u half-page  +/- zoom  Ctrl-c copy  p promote  "
            "F6 kitsunekko  F7 pick  q quit"
            f"{pending}{playback}"
        )

    def copy_current_subtitle(self) -> None:
        """Copy the selected cue text to the system clipboard."""

        cue = self.current_cue
        if cue is None or not cue.text.strip():
            self._playback_status = "nothing to copy"
            self.refresh_view()
            return

        try:
            write_clipboard(cue.text)
        except RuntimeError as exc:
            self._playback_status = str(exc)
            self.notify(str(exc), severity="warning")
        else:
            self._playback_status = "copied subtitle"
            self.notify("Copied subtitle")
        self.refresh_view()

    def remote_label(self) -> str:
        source = self.remote_state.source
        external_id = self.remote_state.external_id
        episode_number = self.remote_state.episode_number
        if source is None or external_id is None:
            return ""
        label = f"{source}:{external_id}"
        if episode_number is not None:
            label += f" ep:{episode_number}"
        return label

    def action_promote(self) -> None:
        """Promote the selected track's SRT alongside the source media file.

        Writes the current candidate to `{stem}.srt` next to the source media so
        players like mpv and Jellyfin can autodiscover it. If a sidecar already
        exists, ask for confirmation (default No).
        """

        if not self.tracks:
            self.notify("No subtitle tracks loaded", severity="error")
            return

        track = self.track
        dest = self.audio_source.source_path.with_suffix(".srt")

        if _same_file(track.path, dest):
            self.notify(f"{dest.name} is already promoted")
            return

        if dest.exists():
            self.push_screen(
                ConfirmOverwriteModal(dest), self._apply_promote(track, dest)
            )
        else:
            self._do_promote(track, dest)

    def _apply_promote(
        self, track: SubtitleTrack, dest: Path
    ) -> Callable[[bool], None]:
        """Return a modal callback that promotes iff the user confirmed."""

        def callback(confirmed: bool) -> None:
            if confirmed:
                self._do_promote(track, dest)

        return callback

    def _do_promote(self, track: SubtitleTrack, dest: Path) -> None:
        """Copy the selected track's SRT to the sidecar destination."""

        try:
            _copy_file_contents_atomic(track.path, dest)
        except OSError as exc:
            self.notify(f"Failed to write {dest.name}: {exc}", severity="error")
        else:
            self.notify(f"Promoted to {dest.name}")

    def action_open_remote_lookup(self) -> None:
        self.push_screen(RemoteLookupModal(self.remote_state), self.apply_remote_lookup)

    def action_open_remote_file_picker(self) -> None:
        self._open_remote_file_picker("Select one subtitle from the full series list.")

    def apply_remote_lookup(self, request: RemoteLookupRequest | None) -> None:
        if request is None:
            return
        self.remote_state = RemoteLookupState(
            source=request.source,
            external_id=request.external_id,
            episode_number=request.episode_number,
            media_kind=request.media_kind,
            status="fetching...",
        )
        self.refresh_view()
        try:
            added_count, first_idx = self.fetch_remote_tracks()
        except Exception as exc:  # pragma: no cover - transport details vary.
            if self._should_offer_manual_pick(exc):
                self.remote_state.status = "episode not found; pick manually"
                self.notify(
                    "Episode lookup found no match. Opening full-series picker.",
                    severity="warning",
                )
                self._open_remote_file_picker(
                    "Episode lookup returned 404; choose from the full series list."
                )
            else:
                self.remote_state.status = f"fetch failed: {exc}"
                self.notify(str(exc), severity="error")
        else:
            self.remote_state.status = f"fetched {added_count} track(s)"
            if added_count:
                self.track_index = first_idx
                self.select_cue_near_time(self.window_start_s + self.window_s / 2)
        self.refresh_view()

    def fetch_remote_tracks_or_exit(self) -> None:
        try:
            added_count, _ = self.fetch_remote_tracks()
        except Exception as exc:
            if not self._should_offer_manual_pick(exc):
                raise SystemExit(
                    f"Could not fetch Kitsunekko subtitles: {exc}"
                ) from exc
            self.remote_state.status = "episode not found; pick manually"
            self._pending_remote_file_picker_message = (
                "Episode lookup returned 404; choose from the full series list."
            )
            return
        else:
            self.remote_state.status = f"fetched {added_count} track(s)"

    def fetch_remote_tracks(self) -> tuple[int, int]:
        state = self.remote_state
        if state.source is None or state.external_id is None:
            raise ValueError("Choose --anilist or --tvdb before fetching subtitles")
        if state.episode_number is None:
            raise ValueError("Episode could not be parsed; pass --episode or press F6")

        client = HttpKitsunekkoSubtitlesClient()
        response = self._remote_file_list(client)

        sidecar_path = self.audio_source.source_path.with_suffix(".srt")
        insertion_idx = len(self.tracks)
        for i, track in enumerate(self.tracks):
            if track.path == sidecar_path:
                insertion_idx = i
                break

        first_idx = insertion_idx
        added = 0
        for file in response.files:
            if not _remote_file_is_subtitle(file):
                continue
            subtitle_id = str(file.get("subtitle_id") or "")
            if subtitle_id and any(
                existing.subtitle_id == subtitle_id for existing in self.tracks
            ):
                continue
            track = self._track_from_remote_file(client, file)
            self.tracks.insert(insertion_idx, track)
            self.cue_indices.insert(insertion_idx, 0)
            insertion_idx += 1
            added += 1

        if self.track_index >= len(self.tracks):
            self.track_index = max(0, len(self.tracks) - 1)
        return added, first_idx

    def _open_remote_file_picker(self, message: str) -> None:
        try:
            response = self.fetch_remote_series_files()
        except Exception as exc:  # pragma: no cover - transport details vary.
            self.remote_state.status = f"series fetch failed: {exc}"
            self.notify(str(exc), severity="error")
            self.refresh_view()
            return

        files = tuple(file for file in response.files if _remote_file_is_subtitle(file))
        if not files:
            self.remote_state.status = "series has no subtitle files"
            self.notify("Series lookup returned no subtitle files", severity="error")
            self.refresh_view()
            return

        self.remote_state.status = f"series has {len(files)} subtitle file(s)"
        self.refresh_view()
        self.push_screen(
            RemoteFilePickModal(files, message=message),
            self.apply_manual_remote_pick,
        )

    def apply_manual_remote_pick(
        self, request: ManualSubtitlePickRequest | None
    ) -> None:
        if request is None:
            return
        client = HttpKitsunekkoSubtitlesClient()
        try:
            track = self._track_from_remote_file(client, request.file)
        except Exception as exc:  # pragma: no cover - parse and transport details vary.
            self.remote_state.status = f"download failed: {exc}"
            self.notify(str(exc), severity="error")
            self.refresh_view()
            return

        insertion_idx = self._remote_insertion_index()
        self.tracks.insert(insertion_idx, track)
        self.cue_indices.insert(insertion_idx, 0)
        self.track_index = insertion_idx
        self.select_cue_near_time(self.window_start_s + self.window_s / 2)
        self.remote_state.status = "picked 1 track"
        self.notify(f"Loaded {track.path.name}")
        self.refresh_view()

    def fetch_remote_series_files(self) -> KitsunekkoFileListResponse:
        state = self.remote_state
        if state.source is None or state.external_id is None:
            raise ValueError("Choose --anilist or --tvdb before picking subtitles")

        client = HttpKitsunekkoSubtitlesClient()
        if state.source == "anilist":
            return client.anilist_files(state.external_id)
        return client.tvdb_files(state.external_id, media_kind=state.media_kind)

    def _remote_insertion_index(self) -> int:
        sidecar_path = self.audio_source.source_path.with_suffix(".srt")
        for i, track in enumerate(self.tracks):
            if track.path == sidecar_path:
                return i
        return len(self.tracks)

    def _should_offer_manual_pick(self, exc: Exception) -> bool:
        if re.search(r"\b404\b", str(exc)) is None:
            return False
        try:
            response = self.fetch_remote_series_files()
        except Exception:
            return False
        return any(_remote_file_is_subtitle(file) for file in response.files)

    def _remote_file_list(
        self,
        client: HttpKitsunekkoSubtitlesClient,
    ) -> KitsunekkoFileListResponse:
        state = self.remote_state
        assert state.external_id is not None
        assert state.episode_number is not None
        if state.source == "anilist":
            return client.anilist_episode_files(state.external_id, state.episode_number)
        return client.tvdb_episode_files(
            state.external_id,
            state.episode_number,
            media_kind=state.media_kind,
        )

    def _track_from_remote_file(
        self,
        client: HttpKitsunekkoSubtitlesClient,
        file: dict[str, Any],
    ) -> SubtitleTrack:
        subtitle_id = str(file.get("subtitle_id") or "")
        if not subtitle_id:
            raise ValueError(f"Kitsunekko row is missing subtitle_id: {file}")
        repo_path = str(file.get("repo_path") or file.get("filename") or subtitle_id)
        filename = Path(
            str(file.get("filename") or Path(repo_path).name or f"{subtitle_id}.srt")
        ).name
        extension = str(file.get("extension", "")).lower().lstrip(".")
        content = client.file_content(subtitle_id)
        if extension == "ass":
            cues = parse_ass(content.decode("utf-8-sig", errors="replace"))
            local_path = self.download_dir / f"{subtitle_id}-{Path(filename).stem}.srt"
            local_path.write_text(format_srt(cues), encoding="utf-8")
        else:
            local_path = self.download_dir / f"{subtitle_id}-{filename}"
            local_path.write_bytes(content)
            cues = read_srt(local_path)
        return SubtitleTrack(
            path=local_path,
            cues=cues,
            repo_path=repo_path,
            subtitle_id=subtitle_id,
        )

    def toggle_playback(self) -> None:
        if self.is_playing():
            self.stop_playback()
            self._playback_status = "stopped"
            self.refresh_view()
            return

        cue = self.current_cue
        if cue is None:
            self._playback_status = "no subtitle selected"
            self.refresh_view()
            return

        start_s, duration_s = playback_range(cue)
        try:
            self._player.play(start_s, duration_s)
        except RuntimeError as exc:
            self._playback_status = str(exc)
            self.notify(str(exc), severity="error")
        else:
            end_s = start_s + duration_s
            self._playback_status = (
                f"playing {format_clock(start_s)} -> {format_clock(end_s)}"
            )
        self.refresh_view()

    def stop_playback(self) -> None:
        self._player.stop()

    def is_playing(self) -> bool:
        return self._player.is_playing()

    def playback_status(self) -> str:
        if self.is_playing():
            return self._playback_status
        if self._playback_status.startswith("playing "):
            self._playback_status = "playback finished"
        return self._playback_status

    def _activity_bar(
        self,
        *,
        width: int,
        start_s: float,
        end_s: float,
        active_cue: SubtitleCue | None,
    ) -> Text:
        text = Text()
        step_s = (end_s - start_s) / width
        for cell in range(width):
            cell_start_s = start_s + cell * step_s
            cell_end_s = cell_start_s + step_s
            cue_index = self._visible_cue_index(cell_start_s, cell_end_s)
            if cue_index is None:
                text.append(GAP_BLOCK, style=GAP_STYLE)
                continue

            cue = self.track.cues[cue_index]
            if active_cue is not None and cue.index == active_cue.index:
                text.append(SPAN_BLOCK, style=ACTIVE_SPAN_STYLE)
            else:
                text.append(SPAN_BLOCK, style=SPAN_STYLES[cue_index % len(SPAN_STYLES)])
        return text

    def _visible_cue_index(self, cell_start_s: float, cell_end_s: float) -> int | None:
        best_index = None
        best_overlap_s = 0.0
        for index, cue in enumerate(self.track.cues):
            overlap_s = min(cue.end_s, cell_end_s) - max(cue.start_s, cell_start_s)
            if overlap_s > best_overlap_s:
                best_index = index
                best_overlap_s = overlap_s
        return best_index

    def _tick_bar(self, *, width: int, start_s: float, end_s: float) -> Text:
        characters = [" " for _ in range(width)]
        labels = [
            format_clock(start_s),
            format_clock((start_s + end_s) / 2),
            format_clock(end_s),
        ]
        positions = [
            0,
            max(0, width // 2 - len(labels[1]) // 2),
            max(0, width - len(labels[2])),
        ]
        for label, position in zip(labels, positions, strict=True):
            for offset, character in enumerate(label):
                target = position + offset
                if 0 <= target < width:
                    characters[target] = character
        return Text("".join(characters), style="dim")

    def _focus_time_s(self) -> float:
        cue = self.current_cue
        if cue is not None:
            return (cue.start_s + cue.end_s) / 2
        return self.window_start_s + self.window_s / 2

    def _clear_g_pending(self) -> None:
        if self._pending_g:
            self._pending_g = False
            self.refresh_view()


def format_clock(seconds: float) -> str:
    milliseconds_total = max(0, round(seconds * 1000))
    hours, remainder = divmod(milliseconds_total, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, milliseconds = divmod(remainder, 1000)
    if hours:
        return f"{hours:d}:{minutes:02d}:{whole_seconds:02d}.{milliseconds:03d}"
    return f"{minutes:02d}:{whole_seconds:02d}.{milliseconds:03d}"


def format_duration(seconds: float) -> str:
    if seconds >= 3600:
        return format_clock(seconds)
    if seconds >= 60:
        minutes, remainder = divmod(seconds, 60)
        return f"{int(minutes)}m{remainder:04.1f}s"
    return f"{seconds:.1f}s"


def playback_range(cue: SubtitleCue) -> tuple[float, float]:
    start_s = max(0.0, cue.start_s)
    end_s = max(start_s, cue.end_s)
    return start_s, max(0.001, end_s - start_s)


def parse_ass(text: str, *, source_path: str | Path | None = None) -> list[SubtitleCue]:
    """Parse ASS dialogue events into plain subtitle cues for timing review."""

    events = False
    fields: list[str] = []
    cues: list[SubtitleCue] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(";"):
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            events = stripped.lower() == "[events]"
            continue
        if not events:
            continue
        if stripped.lower().startswith("format:"):
            fields = [
                field.strip().lower() for field in stripped.split(":", 1)[1].split(",")
            ]
            continue
        if not stripped.lower().startswith("dialogue:") or not fields:
            continue

        values = stripped.split(":", 1)[1].split(",", max(0, len(fields) - 1))
        if len(values) != len(fields):
            continue
        row = dict(zip(fields, values, strict=True))
        try:
            start_s = parse_ass_timestamp(row["start"])
            end_s = parse_ass_timestamp(row["end"])
        except (KeyError, ValueError):
            continue
        cue_text = clean_ass_text(row.get("text", ""))
        cues.append(
            SubtitleCue(
                source_path=None if source_path is None else str(source_path),
                index=len(cues) + 1,
                start_s=start_s,
                end_s=end_s,
                text=cue_text,
            )
        )

    if not cues:
        label = f" from {source_path}" if source_path else ""
        raise ValueError(f"No ASS dialogue cues found{label}")
    return cues


def parse_ass_timestamp(value: str) -> float:
    parts = value.strip().split(":")
    if len(parts) != 3:
        raise ValueError(f"Invalid ASS timestamp: {value!r}")
    hours = int(parts[0])
    minutes = int(parts[1])
    seconds = float(parts[2])
    return hours * 3600 + minutes * 60 + seconds


def clean_ass_text(value: str) -> str:
    text = value.replace(r"\N", "\n").replace(r"\n", "\n").replace(r"\h", " ")
    cleaned: list[str] = []
    in_override = False
    for char in text:
        if char == "{":
            in_override = True
            continue
        if char == "}":
            in_override = False
            continue
        if not in_override:
            cleaned.append(char)
    return "".join(cleaned).strip()


def _remote_file_is_subtitle(file: dict[str, Any]) -> bool:
    extension = str(file.get("extension", "")).lower().lstrip(".")
    return extension in {"srt", "ass"}


def _remote_file_option_label(file: dict[str, Any]) -> Text:
    text = Text()
    episode = file.get("episode_local") or file.get("episode_absolute")
    if episode is not None:
        text.append(f"ep {episode}  ", style="bold cyan")
    text.append(str(file.get("filename") or file.get("repo_path") or "<unnamed>"))
    group = file.get("group_hint")
    if group:
        text.append(f"  [{group}]", style="dim")
    return text


def _remote_file_match_sort_key(file: dict[str, Any], query: str) -> tuple[int, str]:
    haystack = _remote_file_search_text(file)
    score = _fuzzy_score(query, haystack)
    return (-score, haystack)


def _remote_file_search_text(file: dict[str, Any]) -> str:
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
    """Score a query as an ordered subsequence, favoring compact matches."""

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


def write_clipboard(text: str) -> None:
    """Write text to the user's desktop clipboard using common local tools."""

    command = clipboard_command()
    if command is None:
        raise RuntimeError("clipboard command not found")
    try:
        subprocess.run(
            command,
            input=text.encode("utf-8"),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError("clipboard copy failed") from exc


def clipboard_command() -> list[str] | None:
    """Return the first available command for writing clipboard text."""

    for command in (
        ["pbcopy"],
        ["wl-copy"],
        ["xclip", "-selection", "clipboard"],
        ["xsel", "--clipboard", "--input"],
    ):
        if shutil.which(command[0]) is not None:
            return command
    return None
