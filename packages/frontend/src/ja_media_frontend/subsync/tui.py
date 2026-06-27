from __future__ import annotations

import shutil  # Compatibility seam for an existing promotion regression test.
import tempfile
from pathlib import Path
from typing import Callable

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
)
from ja_media_frontend.subsync.dialogs import ConfirmOverwriteModal
from ja_media_frontend.subsync.models import RemoteLookupState
from ja_media_frontend.widgets.timeline import TimelineWidget, format_clock
from ja_media_core.kitsunekko import HttpKitsunekkoSubtitlesClient


from ja_media_core.subtitle_lid import (
    SubtitleLanguage,
    SubtitleLanguageIdConfig,
)
from ja_media_core.transcripts import SubtitleCue
from ja_media_core.vad import VadOptions
from ja_media_frontend.subsync.service import (
    SubtitleTrack,
    build_subtitle_track,
    promote_subtitle,
    sidecar_path,
)
from ja_media_frontend.subsync.startup import resolve_srt_inputs, run_subsync_tui
from ja_media_frontend.subsync.remote import SubsyncRemoteMixin
from ja_media_frontend.subsync.vad import SubsyncVadMixin
from ja_media_frontend.subsync.interaction import (
    SubsyncInteractionMixin,
    playback_range,
    write_clipboard,
)

_USE_PLAYBACK_PATH = object()


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


class SubsyncTuiApp(
    SubsyncVadMixin,
    SubsyncInteractionMixin,
    SubsyncRemoteMixin,
    App[None],
):
    """A first-pass Textual shell for inspecting subtitle timing activity."""

    BINDINGS = [
        ("f1", "open_help", "Help"),
        ("f6", "open_remote_lookup", "Kitsunekko"),
        ("f7", "open_remote_file_picker", "Pick subtitle"),
        ("t", "open_vad_threshold", "VAD threshold"),
        ("[", "set_cue_mode_vad", "Cue: VAD"),
        ("]", "set_cue_mode_srt", "Cue: SRT"),
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
        promotion_target: Path | None | object = _USE_PLAYBACK_PATH,
        audio_status: str = "",
        vad_backend=None,
        vad_options: VadOptions | None = None,
        vad_audio_path: Path | None = None,
    ) -> None:
        super().__init__()
        self.audio_source = audio_source
        self.promotion_target = (
            audio_source.source_path
            if promotion_target is _USE_PLAYBACK_PATH
            else promotion_target
        )
        self.audio_status = audio_status
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
        self.init_vad(
            vad_backend,
            vad_options=vad_options,
            vad_audio_path=vad_audio_path,
        )

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
        self.on_vad_mount()
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

    def _new_subtitle_client(self) -> HttpKitsunekkoSubtitlesClient:
        """Construct through this module so existing test seams stay stable."""

        return HttpKitsunekkoSubtitlesClient()

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
                layers=self.timeline_layers(),
                start_s=self.window_start_s,
                duration_s=self.window_s,
                title=self._timeline_title(),
                active_span=self.current_cue,
                active_layer=self.active_timeline_layer(),
            )
        else:
            timeline.set_timeline(
                layers=self.timeline_layers(),
                start_s=self.window_start_s,
                duration_s=self.window_s,
                title="No subtitles",
                active_layer=self.active_timeline_layer(),
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
        if self.audio_status:
            text.append("  ")
            text.append(self.audio_status, style="dim")
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

    def render_help(self) -> str:
        pending = "  g..." if self._pending_g else ""
        # The bottom bar only lists the bindings that are easy to forget.
        # Vim-style movement (hjkl/gg/G), space, and q are obvious enough to
        # live in the F1 help modal, and the F-key bindings (F1/F6/F7) are
        # already auto-rendered by Textual's Footer from BINDINGS, so listing
        # them here would just duplicate that row. Playback state lives next
        # to the current cue's timespan (see render_active_cue), not here.
        promote = "p promote" if self.promotion_target is not None else "promotion disabled"
        mode = f"[{self.cue_mode}]"
        return (
            f"{mode} t VAD threshold  Ctrl-f/b page  Ctrl-d/u half-page  +/- zoom  Ctrl-c copy  "
            f"{promote}{pending}"
        )

    def copy_current_subtitle(self) -> None:
        """Copy the selected cue text to the system clipboard."""

        cue = self.current_cue
        cue_text = getattr(cue, "text", "") if cue is not None else ""
        if not cue_text.strip():
            self._playback_status = "nothing to copy"
            self.refresh_view()
            return

        try:
            write_clipboard(cue_text)
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
        if self.promotion_target is None:
            self.notify(
                "Promotion is disabled without a supplied media file",
                severity="warning",
            )
            return

        track = self.track
        dest = sidecar_path(self.promotion_target)

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
                self.promotion_target,
                overwrite=dest.exists(),
            )
        except OSError as exc:
            self.notify(f"Failed to write {dest.name}: {exc}", severity="error")
        else:
            self.notify(f"Promoted to {dest.name}")

def format_duration(seconds: float) -> str:
    if seconds >= 3600:
        return format_clock(seconds)
    if seconds >= 60:
        minutes, remainder = divmod(seconds, 60)
        return f"{int(minutes)}m{remainder:04.1f}s"
    return f"{seconds:.1f}s"
