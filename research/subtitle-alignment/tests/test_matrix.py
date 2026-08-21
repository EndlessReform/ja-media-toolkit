from pathlib import Path

import duckdb

from ja_media_core.transcripts import SubtitleCue

from subtitle_alignment.matrix_methods import (
    FULL_EPISODE_OFFSET_LIMIT_S,
    method_specs,
)
from subtitle_alignment.matrix_sample import (
    MatrixPair,
    _diverse_stratified_sample,
    select_pairs,
)
from subtitle_alignment.matrix_transform import infer_transform


def _cue(index: int, start: float, end: float) -> SubtitleCue:
    return SubtitleCue(None, index, start, end, f"cue {index}")


def test_constant_only_arms_disable_every_scale_path() -> None:
    methods = {method.key: method for method in method_specs()}

    assert methods["alass-global"].args == (
        "--no-split",
        "--disable-fps-guessing",
    )
    assert "--no-fix-framerate" in methods["ffsubsync-global"].args
    assert "--skip-infer-framerate-ratio" in methods["ffsubsync-global"].args
    assert methods["ffsubsync-global"].args[-1] == FULL_EPISODE_OFFSET_LIMIT_S
    assert set(methods) == {
        "identity", "alass-global", "ffsubsync-global",
        "alass-piecewise-p5", "ffsubsync-piecewise-p5",
    }


def test_matrix_selects_best_identity_pair_per_episode(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset"
    identity = tmp_path / "identity"
    dataset.mkdir()
    identity.mkdir()
    with duckdb.connect(str(dataset / "evaluation.duckdb")) as connection:
        connection.execute(
            """CREATE TABLE embedded_subtitles (
                 subtitle_input_id VARCHAR, relative_path VARCHAR
               );
               INSERT INTO embedded_subtitles VALUES
                 ('weak-anchor', 'weak.srt'), ('best-anchor', 'best.srt'),
                 ('other-anchor', 'other.srt');
               CREATE TABLE kitsunekko_candidates (
                 subtitle_id VARCHAR, relative_path VARCHAR
               );
               INSERT INTO kitsunekko_candidates VALUES
                 ('candidate-1', 'candidate-1.srt'),
                 ('candidate-2', 'candidate-2.srt');"""
        )
    with duckdb.connect(str(identity / "gate1-identity.duckdb")) as connection:
        connection.execute(
            """CREATE TABLE identity_pair_scores AS SELECT * FROM (VALUES
                 (1, 1, 'weak-anchor', 'candidate-1', 'subrip', 'srt',
                  'weak.srt', 0.1, 0.1, 'scored'),
                 (1, 1, 'best-anchor', 'candidate-1', 'subrip', 'srt',
                  'best.srt', 0.9, 0.8, 'scored'),
                 (1, 2, 'other-anchor', 'candidate-2', 'subrip', 'srt',
                  'other.srt', 0.7, 0.6, 'scored')
               ) AS scores(anilist_id, episode, anchor_id, candidate_id,
                 anchor_serialization, candidate_extension,
                 candidate_repo_path, anchor_fit_score, goodness_of_fit,
                 status)"""
        )

    selected = select_pairs(dataset, identity, sample_size=2)

    assert {pair.anchor_id for pair in selected} == {"best-anchor", "other-anchor"}


def test_matrix_sample_prefers_unseen_series_across_score_buckets() -> None:
    def pair(series: int, episode: int, bucket: int) -> MatrixPair:
        identifier = f"{series}-{episode}"
        return MatrixPair(
            pair_id=identifier, anilist_id=series, episode=episode,
            anchor_id=f"a-{identifier}", candidate_id=f"c-{identifier}",
            anchor_format="subrip", candidate_format="srt",
            anchor_source=f"a-{identifier}.srt",
            candidate_source=f"c-{identifier}.srt",
            candidate_repo_path=f"series/{identifier}.srt",
            identity_decile=bucket, identity_score=0.5, identity_goodness=0.5,
        )

    selected = _diverse_stratified_sample(
        [pair(1, 1, 1), pair(2, 1, 1), pair(1, 2, 2), pair(3, 1, 2)],
        sample_size=4, buckets=2,
    )

    assert [item.anilist_id for item in selected[:3]] == [1, 3, 2]


def test_matrix_sample_does_not_repeat_while_another_bucket_has_new_series() -> None:
    def pair(series: int, episode: int, bucket: int) -> MatrixPair:
        identifier = f"{series}-{episode}"
        return MatrixPair(
            pair_id=identifier, anilist_id=series, episode=episode,
            anchor_id=f"a-{identifier}", candidate_id=f"c-{identifier}",
            anchor_format="subrip", candidate_format="srt",
            anchor_source=f"a-{identifier}.srt",
            candidate_source=f"c-{identifier}.srt",
            candidate_repo_path=f"series/{identifier}.srt",
            identity_decile=bucket, identity_score=0.5, identity_goodness=0.5,
        )

    selected = _diverse_stratified_sample(
        [pair(1, 1, 1), pair(1, 2, 1), pair(2, 1, 2), pair(3, 1, 2)],
        sample_size=3, buckets=2,
    )

    assert {item.anilist_id for item in selected} == {1, 2, 3}


def test_realized_transform_recovers_scale_offsets_and_blocks() -> None:
    source = (
        _cue(1, 10.0, 12.0),
        _cue(2, 20.0, 22.0),
        _cue(3, 30.0, 32.0),
    )
    aligned = (
        _cue(1, 11.5, 13.6),
        _cue(2, 22.0, 24.1),
        _cue(3, 34.5, 36.6),
    )

    facts, cues = infer_transform(source, aligned)

    assert abs(facts.scale - 1.05) < 1e-9
    assert facts.offset_blocks == 2
    assert abs(facts.median_offset_s - 1.0) < 1e-9
    assert facts.reconstruction_rmse_ms < 1e-9
    assert [row["offset_block_start"] for row in cues] == [True, False, True]


def test_piecewise_penalty_arms_are_staged_symmetrically() -> None:
    methods = method_specs()
    penalties = {
        (method.tool, method.split_penalty)
        for method in methods
        if method.piecewise_enabled
    }

    assert penalties == {("alass", 5), ("ffsubsync", 5)}
