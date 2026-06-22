from pathlib import Path

import pytest

from ja_media_core.subsync import infer_anilist_id
from ja_media_frontend.subsync.models import initial_remote_lookup_state


def test_infers_anilist_id_from_derived_audio_parent() -> None:
    media = Path("/media/derived-audio/anilist-101573/S01E001.m4a")

    assert infer_anilist_id(media) == 101573


def test_uses_nearest_matching_anilist_ancestor() -> None:
    media = Path(
        "/media/anilist-999/collection/anilist-101573/Season 01/S01E001.m4a"
    )

    assert infer_anilist_id(media) == 101573


def test_rejects_noncanonical_anilist_directory_names() -> None:
    media = Path("/media/derived-audio/anilist-Bloom-101573/S01E001.m4a")

    assert infer_anilist_id(media) is None


def test_initial_lookup_infers_derived_audio_series_and_episode() -> None:
    media = Path("/media/derived-audio/anilist-101573/S01E001.m4a")

    state = initial_remote_lookup_state(
        media,
        anilist_id=None,
        tvdb_id=None,
        episode_number=None,
        tvdb_media_kind="tv",
    )

    assert state.source == "anilist"
    assert state.external_id == 101573
    assert state.episode_number == 1


def test_explicit_tvdb_overrides_anilist_parent() -> None:
    media = Path("/media/derived-audio/anilist-101573/S01E001.m4a")

    state = initial_remote_lookup_state(
        media,
        anilist_id=None,
        tvdb_id=355156,
        episode_number=2,
        tvdb_media_kind="tv",
    )

    assert state.source == "tvdb"
    assert state.external_id == 355156
    assert state.episode_number == 2


def test_rejects_conflicting_explicit_ids() -> None:
    with pytest.raises(ValueError, match="Pass only one"):
        initial_remote_lookup_state(
            Path("/media/anilist-101573/S01E001.m4a"),
            anilist_id=101573,
            tvdb_id=355156,
            episode_number=None,
            tvdb_media_kind="tv",
        )
