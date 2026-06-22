from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from textual.app import App, ComposeResult
from textual.widgets import (
    Footer,
    Header,
    Static,
)

from ja_media_frontend.audio import (
    MaterializedAudio,
    MaterializedAudioPlayer,
    materialize_audio,
)
from ja_media_frontend.subsync.dialogs import (
    ConfirmOverwriteModal,
    HelpModal,
    RemoteFilePickModal,
    RemoteLookupModal,
)
from ja_media_frontend.subsync.models import (
    ManualSubtitlePickRequest,
    RemoteLookupRequest,
    RemoteLookupState,
    initial_remote_lookup_state,
)
from ja_media_frontend.widgets.timeline import TimelineWidget, format_clock
from ja_media_core.kitsunekko import (
    HttpKitsunekkoSubtitlesClient,
    KitsunekkoFileListResponse,
)
from ja_media_core.config import load_config
from ja_media_core.subtitle_lid import (
    SubtitleLanguage,
    SubtitleLanguageIdConfig,
)
from ja_media_core.transcripts import SubtitleCue
from ja_media_frontend.subsync.service import (
    SubtitleLookup,
    SubtitleTrack,
    build_subtitle_track,
    fetch_episode_files,
    fetch_series_files,
    is_supported_remote_subtitle,
    load_subtitle_track,
    materialize_remote_track,
    promote_subtitle,
    resolve_subtitle_inputs,
    sidecar_path,
)


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
    sort_by_language: bool = False,
) -> None:
    """Load subtitle candidates and run the Textual subsync shell."""

    load_dotenv()

    source = Path(source_path).expanduser().resolve()
    if not source.is_file():
        raise SystemExit(f"subsync source is not a file: {source}")
    if window_s <= 0:
        raise SystemExit("--window-s must be positive")
    try:
        remote_state = initial_remote_lookup_state(
            source,
            anilist_id=anilist_id,
            tvdb_id=tvdb_id,
            episode_number=episode_number,
            tvdb_media_kind=tvdb_media_kind,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    language_id_config = load_config().subtitles.language_id

    # (a) Explicit subtitle inputs — always first, in CLI order.
    srt_paths = resolve_srt_inputs(srt_inputs, allow_empty=True)
    tracks = []
    for path in srt_paths:
        try:
            tracks.append(
                load_subtitle_track(
                    path,
                    language_id_config=language_id_config,
                )
            )
        except ValueError as exc:
            raise SystemExit(f"Could not parse {path}: {exc}") from exc

    with tempfile.TemporaryDirectory(prefix="ja-media-subsync-") as tmpdir:
        session_dir = Path(tmpdir)
        try:
            playback_source = materialize_audio(source)
        except RuntimeError as exc:
            raise SystemExit(str(exc)) from exc
        app = SubsyncTuiApp(
            audio_source=playback_source,
            tracks=tracks,
            initial_window_s=window_s,
            remote_state=remote_state,
            download_dir=session_dir,
            language_id_config=language_id_config,
            sort_by_language=sort_by_language,
        )

        # (c) Remote tracks — inserted between explicit and sidecar.
        if fetch_subs:
            app.fetch_remote_tracks_or_exit()

        # (b) Existing sidecar — always appended at the back so the user can
        # compare their pick against what's already on disk. Skip if already
        # loaded via explicit input or remote fetch.
        _loaded_paths = {t.path for t in app.tracks}
        _sidecar = sidecar_path(source)
        if _sidecar.is_file() and _sidecar not in _loaded_paths:
            try:
                app.tracks.append(
                    load_subtitle_track(
                        _sidecar,
                        language_id_config=language_id_config,
                    )
                )
                app.cue_indices.append(0)
            except ValueError as exc:
                raise SystemExit(f"Could not parse sidecar {_sidecar}: {exc}") from exc

        app.sort_tracks_by_language()
        app.run()


def subtitle_track_with_language(
    *,
    path: Path,
    cues: list[SubtitleCue],
    config: SubtitleLanguageIdConfig,
    repo_path: str | None = None,
    subtitle_id: str | None = None,
) -> SubtitleTrack:
    """Compatibility wrapper for constructing an already-parsed track."""

    return build_subtitle_track(
        path=path,
        cues=cues,
        language_id_config=config,
        repo_path=repo_path,
        subtitle_id=subtitle_id,
    )


def resolve_srt_inputs(inputs: list[str], *, allow_empty: bool = False) -> list[Path]:
    """Compatibility wrapper around shared SRT/ASS input resolution."""

    try:
        return resolve_subtitle_inputs(inputs, allow_empty=allow_empty)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


class SubsyncTuiApp(App[None]):
    """A first-pass Textual shell for inspecting subtitle timing activity."""

    BINDINGS = [
        ("f1", "open_help", "Help"),
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
        margin-bottom: 1;
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
        audio_source: MaterializedAudio,
        tracks: list[SubtitleTrack],
        initial_window_s: float,
        remote_state: RemoteLookupState | None = None,
        download_dir: Path | None = None,
        language_id_config: SubtitleLanguageIdConfig | None = None,
        sort_by_language: bool = False,
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
        self.language_id_config = (
            language_id_config or SubtitleLanguageIdConfig()
        )
        self.sort_by_language = sort_by_language
        self._pending_remote_file_picker_message = ""
        self._pending_g = False
        self._player = MaterializedAudioPlayer(audio_source)
        self._playback_status = ""
        self._playback_poll = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static(id="source")
        yield Static(id="candidates")
        yield TimelineWidget(
            id="timeline",
            empty_message="Press F6 to select a Kitsunekko lookup.",
        )
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
        # Promote the anilist/tvdb selector to the app title once we actually
        # have fetched tracks; before that the generic "ja-media subsync" title
        # is more honest since the lookup may still fail or be redirected.
        remote_label = self.remote_label()
        self.title = remote_label if (remote_label and self.tracks) else "ja-media subsync"
        self.query_one("#source", Static).update(self.render_source())
        self.query_one("#candidates", Static).update(self.render_candidates())
        timeline = self.query_one("#timeline", TimelineWidget)
        if self.tracks:
            timeline.set_timeline(
                self.track.cues,
                start_s=self.window_start_s,
                duration_s=self.window_s,
                title=self._timeline_title(),
                active_span=self.current_cue,
            )
        else:
            timeline.set_timeline(
                (),
                start_s=self.window_start_s,
                duration_s=self.window_s,
                title="No subtitles",
            )
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
        # The anilist/tvdb selector lives in the app title once tracks are
        # fetched (see refresh_view). Before that -- lookup configured but not
        # yet resolved -- keep it here so the user can see what is queued, and
        # surface transient fetch status alongside it.
        remote_label = self.remote_label()
        if remote_label and not self.tracks:
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
            # The LID column was removed to save horizontal real estate; the
            # only language signal we surface inline is a red NON-JA tag, so
            # foreign subs are still obvious at a glance without dedicating a
            # column to the full bucket taxonomy.
            candidate_cell = Text()
            if (
                track.language_analysis is not None
                and track.language_analysis.language is SubtitleLanguage.NON_JAPANESE
            ):
                candidate_cell.append("NON-JA ", style="bold red")
            candidate_cell.append(track.label, style=candidate_style)
            table.add_row(
                Text(">" if selected else " ", style=marker_style),
                candidate_cell,
                Text(cue_label, style="bold" if selected else ""),
                Text(str(len(track.cues))),
                Text(format_duration(track.active_s)),
                Text(format_duration(track.end_s)),
            )
        return table

    def sort_tracks_by_language(self) -> None:
        """Apply opt-in stable LID ordering while retaining track cue state."""

        if not self.sort_by_language or len(self.tracks) < 2:
            return

        selected_track = self.track if self.tracks else None
        cue_by_identity = {
            id(track): self.cue_indices[index]
            for index, track in enumerate(self.tracks)
        }
        self.tracks.sort(key=lambda track: track.language_sort_key)
        self.cue_indices = [
            cue_by_identity.get(id(track), 0) for track in self.tracks
        ]
        if selected_track is not None:
            self.track_index = self.tracks.index(selected_track)

    def _timeline_title(self) -> str:
        """Play-window header: stem only, with the SRT UUID prefixed only when
        multiple loaded tracks share the same stem (so the user can tell the
        colliding downloads apart). The on-disk ``{uuid}-{stem}.srt`` name is
        kept on disk to avoid clobbering; we just hide it in the UI otherwise.
        """

        track = self.track
        stem = track.stem_label
        collisions = sum(1 for other in self.tracks if other.stem_label == stem)
        if collisions > 1 and track.subtitle_id:
            return f"{track.subtitle_id}-{stem}"
        return stem

    def render_active_cue(self) -> Panel:
        if not self.tracks:
            return Panel("No subtitle candidates are loaded.", title="Current subtitle")
        cue = self.current_cue
        if cue is None:
            return Panel("No cues in selected SRT.", title="Current subtitle")

        text = Text()
        text.append(
            f"{cue.index}  {format_clock(cue.start_s)} -> {format_clock(cue.end_s)}",
            style="bold cyan",
        )
        # Playback state used to live in the bottom status bar, where it was
        # redundant with the cue timespan already shown here. Surface it as an
        # orange ▶ right after the timespan so the eye lands on the cue that is
        # currently making sound.
        if self.is_playing():
            text.append(" \u25b6", style="bold orange3")
        text.append("\n")
        text.append(cue.text or "<empty cue>")
        return Panel(text, title="Current subtitle", expand=True)

    def render_help(self) -> str:
        pending = "  g..." if self._pending_g else ""
        # The bottom bar only lists the bindings that are easy to forget.
        # Vim-style movement (hjkl/gg/G), space, and q are obvious enough to
        # live in the F1 help modal, and the F-key bindings (F1/F6/F7) are
        # already auto-rendered by Textual's Footer from BINDINGS, so listing
        # them here would just duplicate that row. Playback state lives next
        # to the current cue's timespan (see render_active_cue), not here.
        return (
            "Ctrl-f/b page  Ctrl-d/u half-page  +/- zoom  Ctrl-c copy  p promote"
            f"{pending}"
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
        dest = sidecar_path(self.audio_source.source_path)

        if track.path == dest or (
            track.path.exists() and dest.exists() and track.path.samefile(dest)
        ):
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
            promote_subtitle(
                track,
                self.audio_source.source_path,
                overwrite=dest.exists(),
            )
        except OSError as exc:
            self.notify(f"Failed to write {dest.name}: {exc}", severity="error")
        else:
            self.notify(f"Promoted to {dest.name}")

    def action_open_remote_lookup(self) -> None:
        self.push_screen(RemoteLookupModal(self.remote_state), self.apply_remote_lookup)

    def action_open_help(self) -> None:
        self.push_screen(HelpModal())

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

        destination = sidecar_path(self.audio_source.source_path)
        insertion_idx = len(self.tracks)
        for i, track in enumerate(self.tracks):
            if track.path == destination:
                insertion_idx = i
                break

        first_idx = insertion_idx
        added = 0
        for file in response.files:
            if not is_supported_remote_subtitle(file):
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

        added_tracks = self.tracks[first_idx:insertion_idx]
        self.sort_tracks_by_language()
        if added_tracks:
            first_idx = min(self.tracks.index(track) for track in added_tracks)
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

        files = tuple(
            file for file in response.files if is_supported_remote_subtitle(file)
        )
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
        self.sort_tracks_by_language()
        self.track_index = self.tracks.index(track)
        self.select_cue_near_time(self.window_start_s + self.window_s / 2)
        self.remote_state.status = "picked 1 track"
        self.notify(f"Loaded {track.path.name}")
        self.refresh_view()

    def fetch_remote_series_files(self) -> KitsunekkoFileListResponse:
        client = HttpKitsunekkoSubtitlesClient()
        return fetch_series_files(client, self._subtitle_lookup(require_episode=False))

    def _remote_insertion_index(self) -> int:
        destination = sidecar_path(self.audio_source.source_path)
        for i, track in enumerate(self.tracks):
            if track.path == destination:
                return i
        return len(self.tracks)

    def _should_offer_manual_pick(self, exc: Exception) -> bool:
        if re.search(r"\b404\b", str(exc)) is None:
            return False
        try:
            response = self.fetch_remote_series_files()
        except Exception:
            return False
        return any(is_supported_remote_subtitle(file) for file in response.files)

    def _remote_file_list(
        self,
        client: HttpKitsunekkoSubtitlesClient,
    ) -> KitsunekkoFileListResponse:
        return fetch_episode_files(client, self._subtitle_lookup(require_episode=True))

    def _subtitle_lookup(self, *, require_episode: bool) -> SubtitleLookup:
        """Translate mutable TUI selector state into the shared request contract."""

        state = self.remote_state
        if state.source is None or state.external_id is None:
            raise ValueError("Choose --anilist or --tvdb before fetching subtitles")
        if require_episode and state.episode_number is None:
            raise ValueError("Episode could not be parsed; pass --episode or press F6")
        return SubtitleLookup(
            source=state.source,
            external_id=state.external_id,
            episode_number=state.episode_number,
            media_kind=state.media_kind,
        )

    def _track_from_remote_file(
        self,
        client: HttpKitsunekkoSubtitlesClient,
        file: dict[str, Any],
    ) -> SubtitleTrack:
        return materialize_remote_track(
            client,
            file,
            download_dir=self.download_dir,
            language_id_config=self.language_id_config,
        )

    def toggle_playback(self) -> None:
        if self.is_playing():
            self.stop_playback()
            self._playback_status = "stopped"
            self._stop_playback_poll()
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
            # Poll only to refresh the UI when sounddevice's asynchronous
            # convenience stream finishes; playback owns no TUI worker thread.
            self._stop_playback_poll()
            self._playback_poll = self.set_interval(
                0.1, self._on_playback_tick
            )
        self.refresh_view()

    def _on_playback_tick(self) -> None:
        if not self.is_playing():
            self._stop_playback_poll()
            self.refresh_view()

    def _stop_playback_poll(self) -> None:
        if self._playback_poll is not None:
            self._playback_poll.stop()
            self._playback_poll = None

    def stop_playback(self) -> None:
        self._player.stop()
        self._stop_playback_poll()

    def is_playing(self) -> bool:
        return self._player.is_playing()

    def playback_status(self) -> str:
        if self.is_playing():
            return self._playback_status
        if self._playback_status.startswith("playing "):
            self._playback_status = "playback finished"
        return self._playback_status

    def _focus_time_s(self) -> float:
        cue = self.current_cue
        if cue is not None:
            return (cue.start_s + cue.end_s) / 2
        return self.window_start_s + self.window_s / 2

    def _clear_g_pending(self) -> None:
        if self._pending_g:
            self._pending_g = False
            self.refresh_view()


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
