"""Conservative episode-resolution policy tests."""

from ja_media_core.bronze import BronzeCaptureManifest, BronzeSeries, BronzeStream

from ja_media_data.products.episode_resolution.metadata import SeriesEpisodeMetadata
from ja_media_data.products.episode_resolution.policy import plan_episode_resolution
from ja_media_data.products.episode_resolution.reasons import (
    episode_signal_failure_reason,
)


def manifest(stem: str, *, capture_id: str = "capture-1") -> BronzeCaptureManifest:
    return BronzeCaptureManifest(
        schema_version=1,
        capture_id=capture_id,
        series=BronzeSeries(namespace="anilist", identifier="15451"),
        source_hint=f"{stem}.mkv",
        stem=stem,
        audio_tracks=(
            BronzeStream(
                object_name=f"{stem}.ac3",
                stream_index=1,
                codec="ac3",
                declared_language="jpn",
                is_default=True,
            ),
        ),
        subtitles=(),
    )


def metadata(
    *, episodes: int | None = 12, media_format: str = "TV"
) -> SeriesEpisodeMetadata:
    return SeriesEpisodeMetadata(
        episode_count=episodes,
        media_format=media_format,
        titles=("Example",),
    )


def test_agreeing_parsers_and_metadata_bounds_accept() -> None:
    plan = plan_episode_resolution(
        manifest("[Group]_Example_Ep03_(ABC12345)"),
        input_data_version="etag-v1",
        metadata=metadata(),
    )

    assert plan.classification == "proposed"
    assert plan.reason == "filename_episode_and_title_match_declared_anilist_entry"
    assert plan.proposal is not None
    assert plan.proposal.episode == "3"
    assert plan.hints[0].method == "ptn+explicit-episode-token"


def test_same_evidence_keeps_stable_claim_and_proposal_ids() -> None:
    first = plan_episode_resolution(
        manifest("Example_Ep03"),
        input_data_version="etag-v1",
        metadata=metadata(),
    )
    second = plan_episode_resolution(
        manifest("Example_Ep03"),
        input_data_version="etag-v1",
        metadata=metadata(),
    )

    assert first.hints[0].hint_id == second.hints[0].hint_id
    assert first.proposal is not None and second.proposal is not None
    assert first.proposal.proposal_id == second.proposal.proposal_id


def test_episode_above_anilist_count_is_invalid() -> None:
    plan = plan_episode_resolution(
        manifest("Example_Ep13"),
        input_data_version="etag-v1",
        metadata=metadata(episodes=12),
    )

    assert plan.proposal is None
    assert plan.issue is not None
    assert plan.issue.kind == "invalid"
    assert (
        plan.reason
        == "filename_episode_exceeds_declared_anilist_episode_count"
    )


def test_range_is_quarantined_instead_of_collapsed() -> None:
    plan = plan_episode_resolution(
        manifest("Example_Ep03-04"),
        input_data_version="etag-v1",
        metadata=metadata(),
    )

    assert plan.proposal is None
    assert plan.reason == "filename_contains_multi_episode_range"


def test_movie_without_episode_is_a_normal_review_outcome() -> None:
    plan = plan_episode_resolution(
        manifest("Example_Movie"),
        input_data_version="etag-v1",
        metadata=metadata(episodes=1, media_format="MOVIE"),
    )

    assert plan.classification == "quarantined"
    assert plan.reason == "declared_anilist_entry_is_movie"
    assert plan.issue is not None


def test_filename_title_must_match_anilist_identity() -> None:
    plan = plan_episode_resolution(
        manifest("Air_Gear_Ep03"),
        input_data_version="etag-v1",
        metadata=SeriesEpisodeMetadata(
            episode_count=13,
            media_format="TV",
            titles=("AIR", "Air TV"),
        ),
    )

    assert plan.proposal is None
    assert plan.reason == "filename_title_not_equal_to_declared_anilist_titles"


def test_missing_anilist_metadata_has_a_literal_reason() -> None:
    plan = plan_episode_resolution(
        manifest("Example_Ep03"),
        input_data_version="etag-v1",
        metadata=None,
    )

    assert plan.reason == "declared_anilist_id_not_found_in_metadata"


def test_missing_anilist_titles_and_episode_count_have_distinct_reasons() -> None:
    without_titles = plan_episode_resolution(
        manifest("Example_Ep03"),
        input_data_version="etag-v1",
        metadata=SeriesEpisodeMetadata(12, "TV", ()),
    )
    without_count = plan_episode_resolution(
        manifest("Example_Ep03"),
        input_data_version="etag-v1",
        metadata=metadata(episodes=None),
    )

    assert without_titles.reason == "declared_anilist_entry_has_no_titles"
    assert without_count.reason == "declared_anilist_entry_has_no_episode_count"


def test_each_episode_signal_failure_has_a_distinct_reason() -> None:
    assert (
        episode_signal_failure_reason(None, ())
        == "filename_has_no_recognizable_episode_number"
    )
    assert (
        episode_signal_failure_reason(3, (3, 4))
        == "filename_contains_multiple_explicit_episode_numbers"
    )
    assert (
        episode_signal_failure_reason(None, (3,))
        == "filename_parser_missed_explicit_episode_number"
    )
    assert (
        episode_signal_failure_reason(3, ())
        == "filename_parser_episode_has_no_explicit_token"
    )
    assert (
        episode_signal_failure_reason(3, (4,))
        == "filename_parser_episode_differs_from_explicit_token"
    )
    assert episode_signal_failure_reason(3, (3,)) is None
