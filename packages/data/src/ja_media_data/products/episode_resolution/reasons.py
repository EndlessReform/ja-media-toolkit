"""Stable, literal outcome codes for the episode-resolution recipe."""

from __future__ import annotations


BRONZE_MANIFEST_FAILED_SCHEMA_VALIDATION = "bronze_manifest_failed_schema_validation"
FILENAME_CONTAINS_MULTI_EPISODE_RANGE = "filename_contains_multi_episode_range"
FILENAME_HAS_NO_RECOGNIZABLE_EPISODE_NUMBER = (
    "filename_has_no_recognizable_episode_number"
)
FILENAME_CONTAINS_MULTIPLE_EXPLICIT_EPISODE_NUMBERS = (
    "filename_contains_multiple_explicit_episode_numbers"
)
FILENAME_PARSER_MISSED_EXPLICIT_EPISODE_NUMBER = (
    "filename_parser_missed_explicit_episode_number"
)
FILENAME_PARSER_EPISODE_HAS_NO_EXPLICIT_TOKEN = (
    "filename_parser_episode_has_no_explicit_token"
)
FILENAME_PARSER_EPISODE_DIFFERS_FROM_EXPLICIT_TOKEN = (
    "filename_parser_episode_differs_from_explicit_token"
)
DECLARED_ANILIST_ID_NOT_FOUND_IN_METADATA = "declared_anilist_id_not_found_in_metadata"
DECLARED_ANILIST_ENTRY_HAS_NO_TITLES = "declared_anilist_entry_has_no_titles"
FILENAME_TITLE_NOT_EQUAL_TO_DECLARED_ANILIST_TITLES = (
    "filename_title_not_equal_to_declared_anilist_titles"
)
DECLARED_ANILIST_ENTRY_HAS_NO_EPISODE_COUNT = (
    "declared_anilist_entry_has_no_episode_count"
)
DECLARED_ANILIST_ENTRY_IS_MOVIE = "declared_anilist_entry_is_movie"
FILENAME_EPISODE_EXCEEDS_DECLARED_ANILIST_COUNT = (
    "filename_episode_exceeds_declared_anilist_episode_count"
)
FILENAME_AND_DECLARED_ANILIST_ENTRY_AGREE = (
    "filename_episode_and_title_match_declared_anilist_entry"
)


def episode_signal_failure_reason(
    parser_episode: int | None, explicit_episodes: tuple[int, ...]
) -> str | None:
    """Name the exact failed episode-signal invariant, if any."""

    if parser_episode is None and not explicit_episodes:
        return FILENAME_HAS_NO_RECOGNIZABLE_EPISODE_NUMBER
    if len(explicit_episodes) > 1:
        return FILENAME_CONTAINS_MULTIPLE_EXPLICIT_EPISODE_NUMBERS
    if parser_episode is None:
        return FILENAME_PARSER_MISSED_EXPLICIT_EPISODE_NUMBER
    if not explicit_episodes:
        return FILENAME_PARSER_EPISODE_HAS_NO_EXPLICIT_TOKEN
    if parser_episode != explicit_episodes[0]:
        return FILENAME_PARSER_EPISODE_DIFFERS_FROM_EXPLICIT_TOKEN
    return None
