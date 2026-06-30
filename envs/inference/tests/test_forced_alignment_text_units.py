from __future__ import annotations

from ja_media_inference.forced_alignment import (
    groups_from_cues,
    groups_from_lines,
    groups_from_text,
    merge_token_alignments_by_group,
    segment_group_with_nagisa,
)
from ja_media_inference.forced_alignment.text_units import TokenAlignment
from ja_media_core.transcripts import SubtitleCue


def test_nagisa_tokens_gobble_back_to_group_alignment() -> None:
    group = groups_from_text("また迷ったら、ここにおいで。")[0]
    tokens = segment_group_with_nagisa(group)

    assert [token.group_id for token in tokens]
    assert "、" not in [token.text for token in tokens]
    assert "。" not in [token.text for token in tokens]

    alignments = [
        TokenAlignment(token=token, start_s=index * 0.25, end_s=index * 0.25 + 0.2)
        for index, token in enumerate(tokens)
    ]

    merged = merge_token_alignments_by_group([group], alignments)

    assert merged[group.id].start_s == 0.0
    assert merged[group.id].end_s == alignments[-1].end_s
    assert merged[group.id].metadata["token_count"] == len(tokens)


def test_text_lines_and_cues_share_group_shape() -> None:
    line_groups = groups_from_lines("一行目です。\n二行目です。", group_prefix="src")
    cue_groups = groups_from_cues(
        [
            SubtitleCue(
                source_path=None,
                index=7,
                start_s=1.0,
                end_s=2.0,
                text="一行目です。",
            )
        ],
        source_id="candidate",
    )

    assert [group.id for group in line_groups] == ["src:0001", "src:0002"]
    assert cue_groups[0].id == "candidate:cue:7"
    assert cue_groups[0].source_cue is not None
