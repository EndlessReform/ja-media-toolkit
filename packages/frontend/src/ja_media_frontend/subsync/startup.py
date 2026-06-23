"""Startup orchestration for the subsync TUI."""

from __future__ import annotations

import tempfile
from pathlib import Path

from dotenv import load_dotenv

from ja_media_core.config import load_config
from ja_media_frontend.audio import materialize_audio
from ja_media_frontend.subsync.audio_source import resolve_subsync_audio
from ja_media_frontend.subsync.models import initial_remote_lookup_state
from ja_media_frontend.subsync.service import (
    load_subtitle_track,
    resolve_subtitle_inputs,
    sidecar_path,
)


def run_subsync_tui(
    *,
    source_path: str | None,
    srt_inputs: list[str],
    window_s: float,
    anilist_id: int | None = None,
    tvdb_id: int | None = None,
    episode_number: int | None = None,
    audio_profile: str = "portable-aac-v1",
    fetch_subs: bool = False,
    tvdb_media_kind: str | None = "tv",
    sort_by_language: bool = False,
) -> None:
    """Resolve playback and subtitle inputs, then run the Textual shell."""

    from ja_media_frontend.subsync.tui import SubsyncTuiApp

    load_dotenv()
    source = (
        Path(source_path).expanduser().resolve()
        if source_path is not None
        else None
    )
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
        audio_selection = resolve_subsync_audio(
            source,
            anilist_id=(
                remote_state.external_id
                if remote_state.source == "anilist"
                else None
            ),
            episode_number=remote_state.episode_number,
            profile=audio_profile,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    language_id_config = load_config().subtitles.language_id

    tracks = []
    for path in resolve_srt_inputs(srt_inputs, allow_empty=True):
        try:
            tracks.append(
                load_subtitle_track(path, language_id_config=language_id_config)
            )
        except ValueError as exc:
            raise SystemExit(f"Could not parse {path}: {exc}") from exc

    with tempfile.TemporaryDirectory(prefix="ja-media-subsync-") as tmpdir:
        try:
            playback_source = materialize_audio(audio_selection.playback_path)
        except RuntimeError as exc:
            raise SystemExit(str(exc)) from exc
        app = SubsyncTuiApp(
            audio_source=playback_source,
            tracks=tracks,
            initial_window_s=window_s,
            remote_state=remote_state,
            download_dir=Path(tmpdir),
            language_id_config=language_id_config,
            sort_by_language=sort_by_language,
            promotion_target=audio_selection.promotion_target,
            audio_status=audio_selection.status,
        )
        if fetch_subs:
            app.fetch_remote_tracks_or_exit()

        loaded_paths = {track.path for track in app.tracks}
        sidecar = (
            sidecar_path(audio_selection.promotion_target)
            if audio_selection.promotion_target is not None
            else None
        )
        if sidecar is not None and sidecar.is_file() and sidecar not in loaded_paths:
            try:
                app.tracks.append(
                    load_subtitle_track(
                        sidecar,
                        language_id_config=language_id_config,
                    )
                )
                app.cue_indices.append(0)
            except ValueError as exc:
                raise SystemExit(f"Could not parse sidecar {sidecar}: {exc}") from exc
        app.sort_tracks_by_language()
        app.run()


def resolve_srt_inputs(inputs: list[str], *, allow_empty: bool = False) -> list[Path]:
    """Translate subtitle validation into the CLI's exit semantics."""

    try:
        return resolve_subtitle_inputs(inputs, allow_empty=allow_empty)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
