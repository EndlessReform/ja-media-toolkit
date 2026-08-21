from __future__ import annotations

from pathlib import Path

from ja_media_core.audio_library import (
    AnimeAudioSeriesMetadata,
    ArtifactRecord,
    AudioStreamProbe,
    EpisodeMapping,
    MaterializationPlan,
    PORTABLE_AAC_V1,
    SourceMediaProbe,
    SubtitleArtifactRecord,
    SubtitleStreamProbe,
)
from ja_media_frontend.audio_library.discovery import identity_search_query
from ja_media_frontend.audio_library.manifest import load_manifest
from ja_media_frontend.audio_library.wizard import execute_ingest_plan


def _plan(source_root: Path, destination_root: Path) -> MaterializationPlan:
    source_path = source_root / "Episode 01.mkv"
    source_path.touch()
    stream = AudioStreamProbe(2, 0, "flac", "jpn", None, 2, 48_000, True)
    source = SourceMediaProbe(
        source_path,
        duration_ms=1000,
        size_bytes=source_path.stat().st_size,
        mtime_ns=source_path.stat().st_mtime_ns,
        audio_streams=(stream,),
    )
    series = AnimeAudioSeriesMetadata(
        anilist_id=42,
        title_english="Example",
        title_native=None,
        title_romaji=None,
        title_preferred="Example",
        description_html=None,
        description_text=None,
        format="TV",
        status=None,
        season=None,
        season_year=None,
        episode_count=1,
        typical_duration_minutes=None,
        start_date=None,
        end_date=None,
        genres=(),
        source=None,
        country_of_origin="JP",
        cover_url=None,
        banner_url=None,
        mal_id=None,
        site_url=None,
        upstream_updated_at=None,
        raw_snapshot={},
    )
    return MaterializationPlan(
        source_root,
        destination_root,
        series,
        (EpisodeMapping("1", source, stream),),
        PORTABLE_AAC_V1,
    )


def test_execution_checkpoints_manifest_and_resumes(monkeypatch, tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    destination_root = tmp_path / "destination"
    source_root.mkdir()
    destination_root.mkdir()
    plan = _plan(source_root, destination_root)

    def fake_materialize(mapping, destination, series, profile):
        destination.write_bytes(b"audio")
        return ArtifactRecord(destination.name, 5, 1000, "aac", 128000, 2, 48000, "hash")

    monkeypatch.setattr(
        "ja_media_frontend.audio_library.executor.materialize_episode",
        fake_materialize,
    )
    monkeypatch.setattr(
        "ja_media_frontend.audio_library.executor.verify_audio_artifact",
        lambda path, profile: ArtifactRecord(path.name, 5, 1000, "aac", 128000, 2, 48000),
    )

    first = execute_ingest_plan(plan)
    second = execute_ingest_plan(plan, resume=True)
    manifest = load_manifest(plan.series_directory / ".ja-media.json")

    assert first.created == ("S01E001.m4a",)
    assert second.skipped == ("S01E001.m4a",)
    assert manifest.episodes[0].source_relative_path == "Episode 01.mkv"


def test_execution_adopts_existing_audio_and_backfills_subtitles(
    monkeypatch, tmp_path: Path
) -> None:
    source_root = tmp_path / "source"
    destination_root = tmp_path / "destination"
    source_root.mkdir()
    destination_root.mkdir()
    plan = _plan(source_root, destination_root)
    subtitle_stream = SubtitleStreamProbe(3, 0, "ass", "eng", "English", True)
    mapping = EpisodeMapping(
        "1",
        plan.mappings[0].source,
        plan.mappings[0].stream,
        (subtitle_stream,),
    )
    plan = MaterializationPlan(
        plan.source_root,
        plan.destination_root,
        plan.series,
        (mapping,),
        plan.profile,
    )
    series_dir = plan.series_directory
    series_dir.mkdir(parents=True)
    (series_dir / "S01E001.m4a").write_bytes(b"audio")

    def fail_materialize(mapping, destination, series, profile):
        raise AssertionError("existing audio should be adopted, not overwritten")

    monkeypatch.setattr(
        "ja_media_frontend.audio_library.executor.materialize_episode",
        fail_materialize,
    )
    monkeypatch.setattr(
        "ja_media_frontend.audio_library.executor.verify_audio_artifact",
        lambda path, profile: ArtifactRecord(
            path.name, 5, 1000, "aac", 128000, 2, 48000, "hash"
        ),
    )
    monkeypatch.setattr(
        "ja_media_frontend.audio_library.executor.materialize_episode_subtitles",
        lambda *args, **kwargs: (
            SubtitleArtifactRecord(
                "stream-3",
                "_subs/S01E001.stream-3.eng.srt",
                10,
                "ass",
                "eng",
                "English",
                True,
                3,
                0,
                "subhash",
            ),
        ),
    )

    summary = execute_ingest_plan(plan)
    manifest = load_manifest(plan.series_directory / ".ja-media.json")

    assert summary.created == ()
    assert summary.skipped == ("S01E001.m4a",)
    assert manifest.episodes[0].artifact.relative_path == "S01E001.m4a"
    assert manifest.episodes[0].subtitles[0].subtitle_id == "stream-3"


def test_execution_materializes_new_delta_after_skipping_existing(
    monkeypatch, tmp_path: Path
) -> None:
    source_root = tmp_path / "source"
    destination_root = tmp_path / "destination"
    source_root.mkdir()
    destination_root.mkdir()
    first = _plan(source_root, destination_root).mappings[0]
    second_path = source_root / "Episode 02.mkv"
    second_path.touch()
    second_source = SourceMediaProbe(
        second_path,
        duration_ms=1000,
        size_bytes=second_path.stat().st_size,
        mtime_ns=second_path.stat().st_mtime_ns,
        audio_streams=first.source.audio_streams,
    )
    plan = _plan(source_root, destination_root)
    plan = MaterializationPlan(
        plan.source_root,
        plan.destination_root,
        plan.series,
        (first, EpisodeMapping("2", second_source, first.stream)),
        plan.profile,
    )

    def fake_materialize(mapping, destination, series, profile):
        destination.write_bytes(b"audio")
        return ArtifactRecord(destination.name, 5, 1000, "aac", 128000, 2, 48000, "hash")

    monkeypatch.setattr(
        "ja_media_frontend.audio_library.executor.materialize_episode",
        fake_materialize,
    )
    monkeypatch.setattr(
        "ja_media_frontend.audio_library.executor.verify_audio_artifact",
        lambda path, profile: ArtifactRecord(
            path.name, 5, 1000, "aac", 128000, 2, 48000, "hash"
        ),
    )

    first_summary = execute_ingest_plan(
        MaterializationPlan(
            plan.source_root,
            plan.destination_root,
            plan.series,
            (first,),
            plan.profile,
        )
    )
    second_summary = execute_ingest_plan(plan)

    assert first_summary.created == ("S01E001.m4a",)
    assert second_summary.skipped == ("S01E001.m4a",)
    assert second_summary.created == ("S01E002.m4a",)


def test_identity_search_query_climbs_past_bare_season_directories() -> None:
    assert (
        identity_search_query(Path("/media/anime/Bloom Into You/Season 01"))
        == "Bloom Into You"
    )
    assert identity_search_query(Path("/media/anime/Frieren/S02")) == "Frieren"
    assert (
        identity_search_query(Path("/media/anime/Show/Season 01/S01"))
        == "Show"
    )


def test_identity_search_query_keeps_titles_containing_season_words() -> None:
    assert (
        identity_search_query(Path("/media/anime/Season of the Witch"))
        == "Season of the Witch"
    )
