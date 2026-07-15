"""Typed bronze manifest compatibility tests."""

import pytest

from ja_media_core.bronze import BronzeManifestError, parse_bronze_manifest


def test_legacy_manifest_uses_object_layout_for_series() -> None:
    manifest = parse_bronze_manifest(
        {
            "source": "/downloads/Show/Show_Ep03.mkv",
            "stem": "Show_Ep03",
            "audio": {
                "filename": "Show_Ep03.ac3",
                "stream_index": 1,
                "codec": "ac3",
                "declared_language": "jpn",
                "is_default": True,
            },
            "subtitles": [],
        },
        capture_id="capture-legacy",
        manifest_key="audio/anime/bronze/15451/metadata/Show_Ep03.json",
    )

    assert manifest.schema_version == 1
    assert manifest.series.namespace == "anilist"
    assert manifest.series.identifier == "15451"
    assert manifest.stem == "Show_Ep03"
    assert manifest.audio.declared_language == "jpn"


def test_v2_manifest_preserves_explicit_series_and_stream_headers() -> None:
    manifest = parse_bronze_manifest(
        {
            "schema_version": 2,
            "capture_id": "capture-v2",
            "series": {"namespace": "anilist", "id": 15451},
            "source_hint": "Show - 03.mkv",
            "audio": {
                "key": "Show - 03.flac",
                "stream_index": 2,
                "codec": "flac",
                "declared_language": "jpn",
                "is_default": True,
            },
            "subtitles": [
                {
                    "key": "stream_4.srt",
                    "stream_index": 4,
                    "source_codec": "ass",
                    "declared_language": "eng",
                    "is_default": False,
                }
            ],
        },
        capture_id="capture-v2",
        manifest_key="audio/anime/bronze/15451/metadata/Show - 03.json",
    )

    assert manifest.series.identifier == "15451"
    assert manifest.stem == "Show - 03"
    assert manifest.subtitles[0].codec == "ass"


@pytest.mark.parametrize(
    "payload, message",
    [
        ({"schema_version": 7}, "unsupported"),
        ({"audio": {}, "subtitles": []}, "source_hint"),
        (
            {
                "source_hint": "Show.mkv",
                "audio": {"filename": "Show.ac3", "stream_index": "1"},
                "subtitles": [],
            },
            "stream_index",
        ),
    ],
)
def test_invalid_manifest_is_explainable(payload: dict[str, object], message: str) -> None:
    with pytest.raises(BronzeManifestError, match=message):
        parse_bronze_manifest(
            payload,
            capture_id="capture-bad",
            manifest_key="audio/anime/bronze/15451/metadata/bad.json",
        )
