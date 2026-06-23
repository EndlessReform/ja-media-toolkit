from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from unittest.mock import Mock

import numpy as np
import pytest

from ja_media_core.anime_audio import AnimeAudioArtifact, AnimeAudioNotFoundError
from ja_media_frontend.audio import MaterializedAudio
from ja_media_frontend.subsync.audio_source import resolve_subsync_audio
from ja_media_frontend.subsync.service import SubtitleTrack
from ja_media_frontend.subsync.tui import SubsyncTuiApp


def _artifact(content: bytes = b"derived") -> AnimeAudioArtifact:
    return AnimeAudioArtifact(
        anilist_id=101573,
        episode_key="1",
        profile="portable-aac-v1",
        filename="S01E001.m4a",
        size_bytes=len(content),
        duration_ms=1000,
        codec="aac",
        bitrate_bps=128000,
        channels=2,
        sample_rate_hz=48000,
        sha256=hashlib.sha256(content).hexdigest(),
        created_at="2026-01-01T00:00:00+00:00",
        content_url="/content",
    )


def test_cache_hit_avoids_content_fetch_and_keeps_mkv_for_promotion(
    tmp_path: Path,
) -> None:
    source = tmp_path / "episode.mkv"
    source.write_bytes(b"mkv must stay cold")
    artifact = _artifact()
    cached = (
        tmp_path
        / "cache"
        / "anilist-101573"
        / "1"
        / "portable-aac-v1"
        / "S01E001.m4a"
    )
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b"derived")
    client = Mock()
    client.artifact.return_value = artifact

    selection = resolve_subsync_audio(
        source,
        anilist_id=101573,
        episode_number=1,
        cache_dir=tmp_path / "cache",
        client=client,
    )

    assert selection.playback_path == cached
    assert selection.promotion_target == source
    client.content.assert_not_called()


@pytest.mark.parametrize(
    "error",
    [
        AnimeAudioNotFoundError("missing"),
        RuntimeError("connection refused"),
    ],
)
def test_service_miss_or_failure_falls_back_to_supplied_media(
    tmp_path: Path,
    error: Exception,
) -> None:
    source = tmp_path / "episode.mkv"
    source.write_bytes(b"local")
    client = Mock()
    client.artifact.side_effect = error

    selection = resolve_subsync_audio(
        source,
        anilist_id=101573,
        episode_number=1,
        cache_dir=tmp_path / "cache",
        client=client,
    )

    assert selection.playback_path == source
    assert "using supplied media audio" in selection.status


def test_identity_only_fetches_audio_without_promotion_target(tmp_path: Path) -> None:
    client = Mock()
    client.artifact.return_value = _artifact()
    client.content.return_value = b"derived"

    selection = resolve_subsync_audio(
        None,
        anilist_id=101573,
        episode_number=1,
        cache_dir=tmp_path / "cache",
        client=client,
    )

    assert selection.playback_path.read_bytes() == b"derived"
    assert selection.promotion_target is None


def test_identity_only_requires_complete_identity() -> None:
    with pytest.raises(ValueError, match="both --anilist and --episode"):
        resolve_subsync_audio(None, anilist_id=101573, episode_number=None)


def test_identity_only_tui_explains_and_disables_promotion(tmp_path: Path) -> None:
    playback = tmp_path / "S01E001.m4a"
    playback.write_bytes(b"derived")
    subtitle = tmp_path / "candidate.srt"
    subtitle.write_text("", encoding="utf-8")
    audio = MaterializedAudio(
        source_path=playback,
        sample_rate=48_000,
        samples=np.zeros((48_000, 1), dtype=np.int16),
    )
    app = SubsyncTuiApp(
        audio_source=audio,
        tracks=[SubtitleTrack(subtitle, [])],
        initial_window_s=10.0,
        promotion_target=None,
    )

    app.action_promote()

    assert "promotion disabled" in app.render_help()
    assert not playback.with_suffix(".srt").exists()


def test_cached_playback_promotes_beside_original_mkv(tmp_path: Path) -> None:
    async def run_app() -> None:
        original = tmp_path / "episode.mkv"
        cached = tmp_path / "cache" / "S01E001.m4a"
        subtitle = tmp_path / "candidate.srt"
        original.write_bytes(b"mkv")
        cached.parent.mkdir()
        cached.write_bytes(b"derived")
        subtitle.write_text("candidate", encoding="utf-8")
        audio = MaterializedAudio(
            source_path=cached,
            sample_rate=48_000,
            samples=np.zeros((48_000, 1), dtype=np.int16),
        )
        app = SubsyncTuiApp(
            audio_source=audio,
            tracks=[SubtitleTrack(subtitle, [])],
            initial_window_s=10.0,
            promotion_target=original,
        )
        async with app.run_test() as pilot:
            app.action_promote()
            await pilot.pause()

    asyncio.run(run_app())

    assert (tmp_path / "episode.srt").read_text(encoding="utf-8") == "candidate"
    assert not (tmp_path / "cache" / "S01E001.srt").exists()
