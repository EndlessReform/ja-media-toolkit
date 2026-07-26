import pytest

from ja_media_core.http import ServiceHttpError

from subtitle_alignment.objects import _fetch
from subtitle_alignment.identity import _parse_track
from subtitle_alignment.sampling import coverage_decision
from subtitle_alignment.silver import (
    SilverPool,
    apply_temporary_series_allowlist,
    draw_order,
)
from subtitle_alignment.values import positive_episode_number


def test_series_sample_is_seeded_and_order_independent() -> None:
    first = draw_order((5, 1, 3, 2, 4), seed=17)
    second = draw_order((4, 2, 5, 3, 1), seed=17)

    assert first == second
    assert set(first) == {1, 2, 3, 4, 5}


def test_do_not_merge_allowlist_filters_pool_and_fails_closed(tmp_path) -> None:
    pool = SilverPool(
        head=object(),  # type: ignore[arg-type]
        series_episodes={5: (1, 2), 3: (1, 2), 8: (1, 2)},
    )
    allowlist = tmp_path / "DELETETHIS-subs-only-anilist-ids.txt"
    allowlist.write_text("# temporary\n8\n3 # retained\n")

    filtered = apply_temporary_series_allowlist(pool, allowlist)

    assert list(filtered.series_episodes) == [3, 8]
    with pytest.raises(RuntimeError, match="gate is missing"):
        apply_temporary_series_allowlist(pool, tmp_path / "missing.txt")


def test_episode_number_accepts_only_positive_integers() -> None:
    assert positive_episode_number(7) == 7
    assert positive_episode_number("07") == 7
    assert positive_episode_number(0) is None
    assert positive_episode_number("1.5") is None
    assert positive_episode_number(True) is None


def test_missing_advertised_candidate_is_retained_as_evidence() -> None:
    class MissingClient:
        def file_content(self, subtitle_id: str) -> bytes:
            raise ServiceHttpError("missing", status_code=404)

    assert _fetch(MissingClient(), "stale-id") == (  # type: ignore[arg-type]
        "stale-id",
        (None, "http_404"),
    )

    class BrokenClient:
        def file_content(self, subtitle_id: str) -> bytes:
            raise ServiceHttpError("broken", status_code=500)

    assert _fetch(BrokenClient(), "broken-id") == (  # type: ignore[arg-type]
        "broken-id",
        (None, "http_500"),
    )


def test_series_coverage_allows_one_quarter_but_not_more() -> None:
    assert coverage_decision((1, 2, 3, 4), {1, 2, 3}) == (
        "accepted",
        (4,),
    )
    assert coverage_decision((1, 2, 3, 4), {1, 2}) == (
        "rejected",
        (3, 4),
    )
    assert coverage_decision((1,), {1}) == ("rejected", ())


def test_identity_parser_uses_declared_cached_format(tmp_path) -> None:
    body = "1\n00:00:01,000 --> 00:00:02,000\nhello\n"
    path = tmp_path / "object-without-extension"
    path.write_text(body)

    parsed = _parse_track(
        tmp_path,
        {"path": path.name, "format": "subrip"},
    )

    assert parsed.error is None
    assert parsed.cue_count == 1
    assert parsed.active_s == 1.0
