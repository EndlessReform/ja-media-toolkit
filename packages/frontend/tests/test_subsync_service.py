from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock

import pytest

from ja_media_core.subtitle_lid import SubtitleLanguageIdConfig
from ja_media_frontend.subsync.service import (
    SubtitleDestinationExistsError,
    SubtitleLookup,
    fetch_episode_files,
    fetch_series_files,
    load_subtitle_track,
    materialize_remote_track,
    promote_subtitle,
    resolve_subtitle_inputs,
    search_remote_subtitles,
)


SRT_TEXT = (
    "1\n"
    "00:00:01,000 --> 00:00:02,000\n"
    "hello\n"
)

ASS_TEXT = (
    "[Events]\n"
    "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    "Dialogue: 0,0:00:01.00,0:00:02.50,Default,,0,0,0,,{\\i1}hello\\Nworld\n"
)


def test_resolve_subtitle_inputs_accepts_srt_and_ass() -> None:
    with TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        srt = root / "episode.srt"
        ass = root / "episode.ass"
        ignored = root / "episode.txt"
        srt.write_text(SRT_TEXT, encoding="utf-8")
        ass.write_text(ASS_TEXT, encoding="utf-8")
        ignored.write_text("not subtitles", encoding="utf-8")

        resolved = resolve_subtitle_inputs(
            [str(root / "*.srt"), str(ass), str(srt)]
        )

        assert resolved == [srt.resolve(), ass.resolve()]
        with pytest.raises(ValueError, match="expected .srt or .ass"):
            resolve_subtitle_inputs([str(ignored)])


def test_load_subtitle_track_analyzes_a_local_candidate() -> None:
    with TemporaryDirectory() as tmpdir:
        subtitle = Path(tmpdir) / "episode.srt"
        subtitle.write_text(SRT_TEXT, encoding="utf-8")

        track = load_subtitle_track(
            subtitle,
            language_id_config=SubtitleLanguageIdConfig(),
        )

        assert track.path == subtitle.resolve()
        assert track.cues[0].text == "hello"
        assert track.active_s == pytest.approx(1.0)
        assert track.end_s == pytest.approx(2.0)


def test_lookup_helpers_select_the_concrete_client_method() -> None:
    client = Mock()
    anilist_response = Mock()
    tvdb_response = Mock()
    client.anilist_episode_files.return_value = anilist_response
    client.tvdb_files.return_value = tvdb_response

    assert (
        fetch_episode_files(
            client,
            SubtitleLookup(
                source="anilist",
                external_id=395,
                episode_number=16,
            ),
        )
        is anilist_response
    )
    assert (
        fetch_series_files(
            client,
            SubtitleLookup(
                source="tvdb",
                external_id=79099,
                media_kind="tv",
            ),
        )
        is tvdb_response
    )
    client.anilist_episode_files.assert_called_once_with(395, 16)
    client.tvdb_files.assert_called_once_with(79099, media_kind="tv")


def test_materialize_remote_ass_normalizes_it_to_srt() -> None:
    with TemporaryDirectory() as tmpdir:
        client = Mock()
        client.file_content.return_value = ASS_TEXT.encode("utf-8")

        track = materialize_remote_track(
            client,
            {
                "subtitle_id": "abc",
                "repo_path": "series/[Group] Episode 16.ass",
                "filename": "[Group] Episode 16.ass",
                "extension": "ass",
            },
            download_dir=tmpdir,
            language_id_config=SubtitleLanguageIdConfig(),
        )

        assert track.path.suffix == ".srt"
        assert track.subtitle_id == "abc"
        assert track.cues[0].text == "hello\nworld"
        assert "-->" in track.path.read_text(encoding="utf-8")


def test_remote_search_filters_and_ranks_supported_rows() -> None:
    files = [
        {
            "subtitle_id": "english",
            "filename": "[Group] Episode 16 English.srt",
            "extension": "srt",
        },
        {
            "subtitle_id": "notes",
            "filename": "Episode 16 notes.txt",
            "extension": "txt",
        },
        {
            "subtitle_id": "japanese",
            "filename": "[JPGroup] Episode 16.ass",
            "extension": "ass",
            "language_hint": "japanese",
        },
    ]

    matches = search_remote_subtitles(files, "japanese")

    assert [row["subtitle_id"] for row in matches] == ["japanese", "english"]


def test_promote_requires_overwrite_and_does_not_preserve_source_metadata() -> None:
    with TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        media = root / "episode.mkv"
        subtitle = root / "candidate.srt"
        destination = root / "episode.srt"
        media.write_bytes(b"media")
        subtitle.write_text(SRT_TEXT, encoding="utf-8")
        destination.write_text("old", encoding="utf-8")
        track = load_subtitle_track(
            subtitle,
            language_id_config=SubtitleLanguageIdConfig(),
        )

        with pytest.raises(SubtitleDestinationExistsError):
            promote_subtitle(track, media)

        promoted = promote_subtitle(track, media, overwrite=True)

        assert promoted == destination.resolve()
        assert promoted.read_text(encoding="utf-8") == SRT_TEXT
