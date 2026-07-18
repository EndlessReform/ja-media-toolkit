"""Conservative episode-resolution policy tests."""

from ja_media_core.bronze import BronzeCaptureManifest, BronzeSeries, BronzeStream

from ja_media_data.episode_metadata import SeriesEpisodeMetadata
from ja_media_data.episode_resolution import plan_episode_resolution


def manifest(stem: str, *, capture_id: str = "capture-1") -> BronzeCaptureManifest:
    return BronzeCaptureManifest(
        schema_version=1,
        capture_id=capture_id,
        series=BronzeSeries(namespace="anilist", identifier="15451"),
        source_hint=f"{stem}.mkv",
        stem=stem,
        audio=BronzeStream(
            object_name=f"{stem}.ac3",
            stream_index=1,
            codec="ac3",
            declared_language="jpn",
            is_default=True,
        ),
        subtitles=(),
    )


def metadata(*, episodes: int | None = 12, media_format: str = "TV") -> SeriesEpisodeMetadata:
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
    assert plan.reason == "episode_exceeds_anilist_count"


def test_range_is_quarantined_instead_of_collapsed() -> None:
    plan = plan_episode_resolution(
        manifest("Example_Ep03-04"),
        input_data_version="etag-v1",
        metadata=metadata(),
    )

    assert plan.proposal is None
    assert plan.reason == "multi_episode_range"


def test_movie_without_episode_is_a_normal_review_outcome() -> None:
    plan = plan_episode_resolution(
        manifest("Example_Movie"),
        input_data_version="etag-v1",
        metadata=metadata(episodes=1, media_format="MOVIE"),
    )

    assert plan.classification == "quarantined"
    assert plan.reason == "non_episodic_format"
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
    assert plan.reason == "filename_title_disagrees_with_anilist"
