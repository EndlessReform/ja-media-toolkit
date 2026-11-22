from __future__ import annotations

from pathlib import Path

from ja_media_core.transcripts import SubtitleCue
from ja_media_frontend.srt_cleaning.candidate_rules import (
    FLAG_ANGLE_WRAPPED,
    FLAG_INTERNAL_SPEAKER_BOUNDARY,
    FLAG_REPETITIVE_KANA,
    FLAG_VERY_SHORT_KANA,
    FLAG_WHOLE_CUE_QUOTE,
    RULE_LANGUAGE_CAPTION,
    RULE_LEADING_SPEAKER_MARKER,
    RULE_MIDDLE_DOT,
    RULE_PARATEXT,
    RULE_PARENTHETICAL,
    RULE_READING_GLOSS,
    RULE_TERMINAL_HORIZONTAL_BAR,
    RULE_WIDTH,
    apply_candidate_rules,
    screen_candidate_flags,
)
from ja_media_frontend.srt_cleaning.review_models import (
    ReviewCue,
    ReviewDecision,
    ReviewSource,
    ReviewWorkspace,
)
from ja_media_frontend.srt_cleaning.review_rule_flags import summarize_candidate_flags
from ja_media_frontend.srt_cleaning.review_rule_comparison import (
    compare_candidate_rules,
    rule_comparison_needs_review,
)
from ja_media_frontend.srt_cleaning.review_rule_overlay import rule_comparison_diff


def test_candidate_rules_cover_width_gloss_label_and_sfx() -> None:
    result = apply_candidate_rules(
        "（宗介）旧式のＭ６(シックス)･起動（警告音）"
    )

    assert result.text == "旧式のM6・起動"
    assert result.rules == (
        RULE_PARENTHETICAL,
        RULE_READING_GLOSS,
        RULE_WIDTH,
        RULE_MIDDLE_DOT,
    )


def test_candidate_rules_remove_nested_parenthetical_label() -> None:
    result = apply_candidate_rules("（白銀（しろがね）・かぐや）あっ…")

    assert result.text == "あっ…"
    assert result.rules == (RULE_PARENTHETICAL,)


def test_candidate_rules_can_remove_a_whole_parenthetical_cue() -> None:
    result = apply_candidate_rules("（ジェット機の飛行音）")

    assert result.text == ""
    assert result.rules == (RULE_PARENTHETICAL,)


def test_candidate_rules_remove_only_narrow_obvious_paratext() -> None:
    for text in ('次回 「２ BRIDGE×２」', "つづく", "続く…。"):
        result = apply_candidate_rules(text)
        assert result.text == ""
        assert result.rules == (RULE_PARATEXT,)

    assert apply_candidate_rules("次回の授業で説明します").text == "次回の授業で説明します"


def test_language_caption_removal_precedes_parenthesis_stripping() -> None:
    result = apply_candidate_rules("（外国人１：英語）駅はどこですか？")

    assert result.text == ""
    assert result.rules == (RULE_LANGUAGE_CAPTION,)
    assert apply_candidate_rules("（英語教師）教科書を開いて").text == "教科書を開いて"


def test_candidate_rules_only_strip_leading_speaker_marker() -> None:
    assert apply_candidate_rules("≪ 誰か来た").text == "誰か来た"
    assert apply_candidate_rules("〈おはよう〉").text == "〈おはよう〉"
    assert apply_candidate_rules('“今日は２人･一緒だ”').text == '“今日は２人･一緒だ”'
    assert apply_candidate_rules('彼は“はい”と言った').text == '彼は“はい”と言った'
    assert RULE_LEADING_SPEAKER_MARKER in apply_candidate_rules("≪ 誰か来た").rules


def test_candidate_screening_flags_suspicious_shapes_without_changing_them() -> None:
    cases = {
        "〈おい！〉": (FLAG_ANGLE_WRAPPED,),
        "コラ 動くな≪（理科）そうですか？": (FLAG_INTERNAL_SPEAKER_BOUNDARY,),
        '“守ってあげたい”': (FLAG_WHOLE_CUE_QUOTE,),
        "ハハハハハッ": (FLAG_REPETITIVE_KANA,),
        "フッ": (FLAG_VERY_SHORT_KANA,),
    }

    for text, expected in cases.items():
        result = apply_candidate_rules(text)
        assert result.text == text
        assert result.flags == expected
        assert not result.changed
        assert result.flagged

    assert screen_candidate_flags("普通の台詞です") == ()


def test_terminal_horizontal_bar_rule_does_not_touch_katakana_length_mark() -> None:
    result = apply_candidate_rules("美容院に行き―")

    assert result.text == "美容院に行き"
    assert result.rules == (RULE_TERMINAL_HORIZONTAL_BAR,)
    assert apply_candidate_rules("コーヒー").text == "コーヒー"
    assert apply_candidate_rules("待って—").text == "待って—"


def test_candidate_flag_summary_estimates_saved_windows_touched(tmp_path: Path) -> None:
    cues = (
        _cue("〈おい！〉", "edit", "おい！", ("formatting",), window=1, index=1),
        _cue("普通の台詞", "as_is", None, (), window=1, index=2),
        _cue('“手紙です”', "edit", "「手紙です」", ("quotation_style",), window=2, index=3),
    )
    source = ReviewSource(
        anilist_id=1,
        subtitle_id="sub",
        repo_path="show.srt",
        filename="show.srt",
        source_path=tmp_path / "show.srt",
        cleaned_path=None,
        episode_number=1,
        source_sha256="abc",
        cues=cues,
    )
    workspace = ReviewWorkspace(1, "run", tmp_path, (source,))

    summary = summarize_candidate_flags(workspace)

    assert (summary.total_cues, summary.flagged_cues) == (3, 2)
    assert (summary.total_windows, summary.flagged_windows) == (2, 2)
    assert [(row.name, row.cues) for row in summary.rows] == [
        (FLAG_ANGLE_WRAPPED, 1),
        (FLAG_WHOLE_CUE_QUOTE, 1),
    ]


def test_rule_comparison_distinguishes_exact_partial_and_accepted_change() -> None:
    exact = compare_candidate_rules(
        _cue("（宗介）Ｍ６だ", "edit", "M6だ", ("speaker_label", "typography"))
    )
    partial = compare_candidate_rules(
        _cue("（宗介）Ｍ６だ—", "edit", "M6だ-", ("speaker_label", "typography"))
    )
    accepted_cue = _cue("（本当に）そうだ", "as_is", None, ())
    accepted = compare_candidate_rules(accepted_cue)

    assert exact.status == "exact"
    assert partial.status == "partial"
    assert accepted.status == "accepted_changed"
    assert not rule_comparison_needs_review(
        _cue("（宗介）Ｍ６だ", "edit", "M6だ", ("speaker_label", "typography"))
    )
    assert rule_comparison_needs_review(accepted_cue)


def test_rule_overlay_renders_candidate_and_model_target() -> None:
    rendered = rule_comparison_diff(
        _cue("（宗介）Ｍ６だ—", "edit", "M6だ-", ("speaker_label", "typography"))
    )

    baseline, saved, proposed = rendered.renderables
    assert baseline.plain == "current model input — sent to saved model\n（宗介）Ｍ６だ—"
    assert "current model output — returned by saved model" in saved.plain
    assert "proposed deterministic output — simulation only" in proposed.plain
    assert "partly matches saved model" in proposed.plain
    assert any("bright_green" in str(span.style) for span in saved.spans)
    assert any("red" in str(span.style) for span in proposed.spans)


def _cue(
    text: str,
    kind: str,
    cleaned: str | None,
    reasons: tuple[str, ...],
    *,
    window: int | None = None,
    index: int = 1,
) -> ReviewCue:
    original = SubtitleCue(Path("source.srt"), index, 1.0, 2.0, text)
    return ReviewCue(
        original=original,
        decision=ReviewDecision(
            kind=kind,
            text=cleaned,
            reasons=reasons,
            window_number=window,
        ),
        mechanical_text=text,
        mechanically_changed=False,
    )
