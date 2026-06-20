from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from ja_media_core.config import load_config
from ja_media_core.subtitle_lid import (
    SubtitleLanguage,
    SubtitleLanguageIdConfig,
    analyze_subtitle_language,
    evenly_spaced_sample,
)
from ja_media_core.transcripts import SubtitleCue


def cues(*lines: str) -> list[SubtitleCue]:
    return [
        SubtitleCue(
            source_path=None,
            index=index,
            start_s=float(index),
            end_s=float(index + 1),
            text=line,
        )
        for index, line in enumerate(lines, start=1)
    ]


def permissive_config(**overrides: object) -> SubtitleLanguageIdConfig:
    return SubtitleLanguageIdConfig.model_validate(
        {
            "minimum_lines": 1,
            "minimum_characters": 1,
            **overrides,
        }
    )


def test_obvious_japanese_uses_script_profile_without_model() -> None:
    def fail_detector(_: str) -> str:
        pytest.fail("obvious Japanese subtitles should not invoke model LID")

    analysis = analyze_subtitle_language(
        cues(
            "今日はいい天気ですね。",
            "一緒に学校へ行きましょう。",
            "この字幕は日本語です。",
        ),
        config=permissive_config(),
        detector=fail_detector,
    )

    assert analysis.language is SubtitleLanguage.JAPANESE
    assert analysis.sampled is None
    assert analysis.sort_key[0] == 0


def test_strong_latin_profile_is_non_japanese_without_model() -> None:
    def fail_detector(_: str) -> str:
        pytest.fail("obvious foreign subtitles should not invoke model LID")

    analysis = analyze_subtitle_language(
        cues(
            "This is an English subtitle line.",
            "Another sentence appears over here.",
        ),
        config=permissive_config(),
        detector=fail_detector,
    )

    assert analysis.language is SubtitleLanguage.NON_JAPANESE
    assert analysis.sampled is None


def test_sampled_lid_identifies_bilingual_subtitles() -> None:
    lines = (
        "今日は学校へ行きます。",
        "I am going to school today.",
        "明日は家で勉強します。",
        "Tomorrow I will study at home.",
    )

    analysis = analyze_subtitle_language(
        cues(*lines),
        config=permissive_config(obvious_japanese_script_ratio=0.95),
        detector=lambda line: "ja" if "。" in line else "en",
    )

    assert analysis.language is SubtitleLanguage.BILINGUAL
    assert analysis.sampled is not None
    assert analysis.sampled.japanese_ratio == pytest.approx(0.5)
    assert dict(analysis.sampled.top_languages) == {
        "ja": pytest.approx(0.5),
        "en": pytest.approx(0.5),
    }
    assert analysis.sort_key[0] > SubtitleLanguage.UNKNOWN.rank


def test_too_little_text_is_kept_separate_from_foreign_text() -> None:
    analysis = analyze_subtitle_language(
        cues("はい"),
        detector=lambda _: "ja",
    )

    assert analysis.language is SubtitleLanguage.INSUFFICIENT_TEXT
    assert analysis.sampled is None


def test_even_sample_spans_the_complete_subtitle() -> None:
    assert evenly_spaced_sample([str(index) for index in range(9)], 3) == [
        "0",
        "4",
        "8",
    ]


def test_global_config_loads_subtitle_language_id_settings(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "[subtitles.language_id]\n"
        "sample_lines = 24\n"
        "japanese_lid_ratio = 0.55\n",
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.subtitles.language_id.sample_lines == 24
    assert config.subtitles.language_id.japanese_lid_ratio == pytest.approx(
        0.55
    )


def test_config_rejects_inverted_lid_thresholds() -> None:
    with pytest.raises(
        ValidationError,
        match="bilingual_lid_ratio cannot exceed japanese_lid_ratio",
    ):
        SubtitleLanguageIdConfig(
            bilingual_lid_ratio=0.8,
            japanese_lid_ratio=0.6,
        )
