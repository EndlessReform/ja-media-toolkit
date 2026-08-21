from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

from ja_media_core.anime_audio_models import AnimeAudioSubtitle
from ja_media_frontend.subsync.ground_truth import discover_ground_truth_subtitle


SRT_TEXT = (
    "1\n"
    "00:00:01,000 --> 00:00:02,000\n"
    "embedded\n"
)


def _subtitle(
    subtitle_id: str,
    language: str | None,
    *,
    default: bool = False,
    stream_index: int = 1,
) -> AnimeAudioSubtitle:
    return AnimeAudioSubtitle(
        anilist_id=183385,
        episode_key="1",
        subtitle_id=subtitle_id,
        language=language,
        title=None,
        codec="subrip",
        default=default,
        filename=f"{subtitle_id}.srt",
        size_bytes=len(SRT_TEXT),
        source_stream_index=stream_index,
        source_stream_ordinal=stream_index,
        sha256=None,
        content_url=f"/subtitles/{subtitle_id}/content",
    )


def test_ground_truth_prefers_korean_then_english_then_german(tmp_path: Path) -> None:
    client = Mock()
    client.subtitles.return_value = (
        _subtitle("de", "ger", stream_index=3),
        _subtitle("en", "eng", stream_index=2),
        _subtitle("ko", "kor", stream_index=4),
    )
    client.subtitle_content.return_value = SRT_TEXT.encode("utf-8")

    result = discover_ground_truth_subtitle(
        anilist_id=183385,
        episode_number=1,
        download_dir=tmp_path,
        client=client,
    )

    assert result.track is not None
    assert result.track.subtitle_id == "ko"
    assert result.track.cues[0].text == "embedded"
    assert result.status == "truth kor"


def test_ground_truth_soft_fails_when_subtitles_are_missing(tmp_path: Path) -> None:
    client = Mock()
    client.subtitles.return_value = ()

    result = discover_ground_truth_subtitle(
        anilist_id=183385,
        episode_number=1,
        download_dir=tmp_path,
        client=client,
    )

    assert result.track is None
    assert result.status == "no embedded subs"


def test_ground_truth_soft_fails_when_service_errors(tmp_path: Path) -> None:
    client = Mock()
    client.subtitles.side_effect = RuntimeError("offline")

    result = discover_ground_truth_subtitle(
        anilist_id=183385,
        episode_number=1,
        download_dir=tmp_path,
        client=client,
    )

    assert result.track is None
    assert "truth unavailable" in result.status
