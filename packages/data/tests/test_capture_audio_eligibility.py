"""Capture-audio eligibility and storage-key policy tests."""

from ja_media_data.products.canonical_inputs.selection import (
    audio_object_key,
    subtitle_object_key,
)
from ja_media_data.products.capture_audio_eligibility.compiler import (
    ELIGIBLE,
    FIRST_DECLARED_JAPANESE_TRACK,
    INELIGIBLE,
    LEGACY_SINGLE_AUDIO_TRACK,
    NO_DECLARED_JAPANESE_TRACK,
    compile_product,
)
from ja_media_data.storage.bronze import BronzeDocument, BronzeMarker


class _Store:
    bucket = "bronze-v2"


def _document(*, languages: tuple[str, ...], version: int = 2) -> BronzeDocument:
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
    payload["audio" if version == 1 else "audio_tracks"] = (
        tracks[0] if version == 1 else tracks
    )
    return BronzeDocument(
        BronzeMarker(
            "capture-3",
            "captures/v2/15451/metadata/Show_Ep03.json",
            "etag-3",
            100,
            "2026-01-01T00:00:00Z",
        ),
        payload,
    )


def test_v2_selects_first_declared_japanese_track_in_manifest_order() -> None:
    product = compile_product([_document(languages=("eng", "jpn", "jpn"))], _Store())

    decision = product.rows[0]
    assert (decision.status, decision.reason) == (
        ELIGIBLE,
        FIRST_DECLARED_JAPANESE_TRACK,
    )
    assert decision.selected_audio_stream_index == 2
    assert decision.selected_audio_object_key.endswith("Show_Ep03.stream_2.ac3")


def test_v1_keeps_its_only_track_without_reinterpreting_language() -> None:
    product = compile_product([_document(languages=("eng",), version=1)], _Store())

    decision = product.rows[0]
    assert (decision.status, decision.reason) == (ELIGIBLE, LEGACY_SINGLE_AUDIO_TRACK)
    assert decision.selected_audio_stream_index == 1


def test_v2_without_declared_japanese_audio_is_durable_rejection() -> None:
    product = compile_product([_document(languages=("eng", "und"))], _Store())

    decision = product.rows[0]
    assert (decision.status, decision.reason) == (
        INELIGIBLE,
        NO_DECLARED_JAPANESE_TRACK,
    )
    assert decision.selected_audio_object_key is None
    assert '"declared_language":"und"' in decision.available_audio_tracks


def test_audio_object_key_uses_manifest_prefix_without_hard_coding_it() -> None:
    assert (
        audio_object_key(
            "captures/v2/15451/metadata/Show_Ep03.json",
            "Show_Ep03.stream_2.ac3",
        )
        == "captures/v2/15451/Show_Ep03.stream_2.ac3"
    )


def test_subtitle_key_uses_declared_capture_stem_not_v2_manifest_filename() -> None:
    assert subtitle_object_key(
        "captures/v2/15451/metadata/Show_Ep03.v2.json",
        "stream_3.srt",
        capture_stem="Show_Ep03",
    ) == "captures/v2/15451/subs/Show_Ep03/stream_3.srt"
