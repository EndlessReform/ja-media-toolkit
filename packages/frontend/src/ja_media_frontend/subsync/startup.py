"""Startup orchestration for the subsync TUI."""

from __future__ import annotations

import tempfile
from pathlib import Path

from dotenv import load_dotenv

from ja_media_core.audio import full_audio_chunk, probe_audio_source, resolve_audio_source
from ja_media_core.config import load_config
from ja_media_core.vocal_separation import VocalSeparationOptions
from ja_media_frontend.audio import materialize_audio
from ja_media_frontend.subsync.audio_source import resolve_subsync_audio
from ja_media_frontend.subsync.models import initial_remote_lookup_state
from ja_media_frontend.subsync.service import (
    load_subtitle_track,
    resolve_subtitle_inputs,
    sidecar_path,
)
from ja_media_frontend.subsync.vocal_separation import (
    SubsyncVocalSeparationConfig,
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

    load_dotenv()
    config = load_config()
    separation_config = SubsyncVocalSeparationConfig.load()

    from ja_media_frontend.subsync.tui import SubsyncTuiApp
    try:
        from ja_media_apple.vad import MlxAudioVadBackend
        vad_backend = MlxAudioVadBackend()
    except ImportError:
        vad_backend = None

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
    language_id_config = config.subtitles.language_id

    tracks = []
    for path in resolve_srt_inputs(srt_inputs, allow_empty=True):
        try:
            tracks.append(
                load_subtitle_track(path, language_id_config=language_id_config)
            )
        except ValueError as exc:
            raise SystemExit(f"Could not parse {path}: {exc}") from exc

    with tempfile.TemporaryDirectory(prefix="ja-media-subsync-") as tmpdir:
        vad_audio_path, vad_status = _prepare_vad_audio_source(
            audio_selection.playback_path,
            separation_config,
        )
        playback_path = vad_audio_path or audio_selection.playback_path
        try:
            playback_source = materialize_audio(playback_path)
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
            audio_status=_join_status(audio_selection.status, vad_status),
            vad_backend=vad_backend,
            vad_options=config.vad.to_options(),
            vad_audio_path=vad_audio_path,
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


def _prepare_vad_audio_source(
    source_path: Path,
    separation_config: SubsyncVocalSeparationConfig,
) -> tuple[Path | None, str]:
    if not separation_config.enabled:
        return None, "playback/VAD source: original audio"
    try:
        from ja_media_apple.vocal_separation import DemucsVocalSeparationBackend
    except ImportError:
        return None, "playback/VAD source: original audio; Demucs backend unavailable"

    source = resolve_audio_source(source_path, must_exist=True)
    audio_format = probe_audio_source(source)
    chunk = full_audio_chunk(source, audio_format, kind="subsync_vad_source")
    backend = DemucsVocalSeparationBackend(
        model=separation_config.model,
        device=separation_config.device,
        jobs=separation_config.jobs,
        segment_s=separation_config.segment_s,
    )
    try:
        result = backend.separate(
            [
                chunk,
            ],
            options=VocalSeparationOptions(
                stem=separation_config.stem,
                cache_dir=separation_config.cache_dir,
            ),
        )[0]
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
    cache_label = "cache hit" if result.cache_hit else "created"
    return (
        Path(result.stem_chunk.source.locator),
        f"playback/VAD source: {separation_config.stem} stem ({cache_label})",
    )


def _join_status(*parts: str) -> str:
    return " | ".join(part for part in parts if part)
