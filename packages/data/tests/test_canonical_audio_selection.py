"""Canonical audio selection and storage-key policy tests."""

import pytest

from ja_media_core.bronze import parse_bronze_manifest
from ja_media_data.products.canonical_inputs.selection import (
    audio_object_key,
    select_audio_track,
)


def _manifest(*, languages: tuple[str, ...], version: int = 2):
    payload = {
        "schema_version": version,
        "source": "/staging/Show_Ep03.mkv",
        "stem": "Show_Ep03",
        "subtitles": [],
    }
    tracks = [
        {
            "filename": f"Show_Ep03.stream_{index}.ac3",
            "stream_index": index,
            "codec": "ac3",
            "declared_language": language,
            "is_default": index == 1,
        }
        for index, language in enumerate(languages, start=1)
    ]
    if version == 1:
        payload["audio"] = tracks[0]
    else:
        payload["audio_tracks"] = tracks
    return parse_bronze_manifest(
        payload,
        capture_id="capture-3",
        manifest_key="captures/v2/15451/metadata/Show_Ep03.json",
    )


def test_v2_selects_first_declared_japanese_track_in_manifest_order() -> None:
    selected = select_audio_track(_manifest(languages=("eng", "jpn", "jpn")))

    assert selected.stream_index == 2
    assert selected.object_name == "Show_Ep03.stream_2.ac3"


def test_v1_keeps_its_only_track_without_reinterpreting_language() -> None:
    selected = select_audio_track(_manifest(languages=("eng",), version=1))

    assert selected.stream_index == 1


def test_v2_without_declared_japanese_audio_fails_closed() -> None:
    with pytest.raises(ValueError, match="no audio track declared as jpn"):
        select_audio_track(_manifest(languages=("eng", "und")))


def test_audio_object_key_uses_manifest_prefix_without_hard_coding_it() -> None:
    assert (
        audio_object_key(
            "captures/v2/15451/metadata/Show_Ep03.json",
            "Show_Ep03.stream_2.ac3",
        )
        == "captures/v2/15451/Show_Ep03.stream_2.ac3"
    )
