import json

from ja_media_core.transcripts import SubtitleCue

from subtitle_alignment.review_data import (
    ReviewCase,
    Variant,
    append_judgment,
    track_stats,
)


def test_track_stats_make_sparse_anchor_evidence_explicit() -> None:
    cues = (
        SubtitleCue(None, 1, 10.0, 12.0, "one"),
        SubtitleCue(None, 2, 100.0, 103.0, "two"),
    )

    stats = track_stats(cues)

    assert stats.cues == 2
    assert stats.active_s == 5.0
    assert stats.span_s == 93.0


def test_judgments_are_append_only_and_joinable(tmp_path) -> None:
    variant = Variant(
        pair_id="pair", method="alass-global", method_order=1,
        output_path="out.srt", status="scored", score=0.5, gain=0.2,
        scale=1.0, median_offset_s=60.0, min_offset_s=60.0,
        max_offset_s=60.0, max_abs_offset_s=60.0, offset_blocks=1,
        offset_bound_exceeded=True, block_starts_s=(60.0,),
    )
    case = ReviewCase(
        pair_id="pair", anilist_id=1, episode=2, anchor_path="anchor.srt",
        anchor_format="subrip", candidate_path="candidate.srt",
        candidate_format="subrip", candidate_repo_path="series/file.srt",
        identity_decile=1, variants=(variant,),
    )
    path = tmp_path / "annotations.jsonl"

    append_judgment(path, case, variant, "anchor_sparse")
    append_judgment(path, case, variant, "needs_audio")

    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert [row["label"] for row in rows] == ["anchor_sparse", "needs_audio"]
    assert all(row["pair_id"] == "pair" for row in rows)
