"""Coverage semantics, terminal layout and public CLI dispatch."""

import io
import json
from unittest.mock import Mock

import httpx
import pytest
from rich.console import Console

from ja_media_core.anilist_search import AnimeMetadata, SearchResponse, SearchResult
from ja_media_core.kitsunekko import KitsunekkoFileListResponse
from ja_media_frontend.cli import build_parser, main
from ja_media_frontend.subsync import series_cli
from ja_media_frontend.subsync.series_render import render_series
from ja_media_frontend.subsync.series_report import build_report


def file(identity, episode, group=None):
    return {"subtitle_id": identity, "episode_local": episode, "group_hint": group}


def test_counts_versions_duplicates_unknown_groups_and_future_files():
    report = build_report(1, {
        "status": "RELEASING", "episodes": 12,
        "nextAiringEpisode": {"episode": 4, "airingAt": 200},
    }, [file("a", 1, " A "), file("b", 1, "a"), file("c", 1, "B"),
        file("c", 1, "B"), file("d", 3), file("e", 5, "A"),
        file("f", 5, "B"), file("g", None)], now=100)
    assert report.aired == 3
    assert report.covered == 2
    assert report.multiple_groups == 1
    assert report.episodes[0].releases == 3
    assert report.episodes[0].groups == 2
    assert report.episodes[1].unnamed == 1
    assert report.unassigned == 1


@pytest.mark.parametrize("metadata,aired", [
    ({"status": "FINISHED", "episodes": 28.0}, 28),
    ({"status": "FINISHED", "episodes": "28.0"}, 28),
    ({"status": "FINISHED", "episodes": 12.5}, None),
    ({"status": "NOT_YET_RELEASED", "episodes": 12}, 0),
    ({"status": "RELEASING", "nextAiringEpisode": {"episode": 4, "airingAt": 90}}, None),
    ({"status": "RELEASING", "nextAiringEpisode": None}, None),
])
def test_frontier(metadata, aired):
    assert build_report(1, metadata, [], now=100).aired == aired


def test_narrow_grid_keeps_missing_future_and_unnamed_visible():
    report = build_report(1, {
        "status": "RELEASING", "episodes": 12,
        "nextAiringEpisode": {"episode": 4, "airingAt": 200},
    }, [file("a", 1)], now=100)
    output = io.StringIO()
    render_series(report, Console(file=output, width=40, color_system=None))
    text = output.getvalue()
    assert text.count("Releases") > 2
    assert "03*" in text and "12" in text
    assert "0" in text and "—" in text and "?" in text
    assert all(len(line) <= 40 for line in text.splitlines())


def test_parser_rejects_missing_or_conflicting_selectors():
    parser = build_parser()
    for argv in (["subsync", "get-series"], ["subsync", "get-series", "title", "--anilist", "1"]):
        with pytest.raises(SystemExit):
            parser.parse_args(argv)


@pytest.mark.parametrize("width,blocks", [(80, 3), (160, 1), (240, 1)])
def test_episode_grid_uses_available_width(width, blocks):
    report = build_report(1, {"status": "FINISHED", "episodes": 28},
                          [file(str(n), n, "A") for n in range(1, 29)], now=100)
    output = io.StringIO()
    render_series(report, Console(file=output, width=width, color_system=None))
    lines = output.getvalue().splitlines()
    headers = [line for line in lines if "Episode" in line]
    assert len(headers) == blocks
    assert "28*" in headers[-1]
    assert all(len(line) <= width for line in lines)


@pytest.mark.parametrize("total,width,blocks", [
    (12, 240, 1), (13, 240, 1), (24, 240, 2), (26, 240, 2),
    (36, 240, 3), (39, 240, 3), (156, 240, 13), (26, 40, 6),
])
def test_regular_cours_keep_parallel_blocks(total, width, blocks):
    report = build_report(1, {"status": "FINISHED", "episodes": total}, [], now=100)
    output = io.StringIO()
    render_series(report, Console(file=output, width=width, color_system=None))
    lines = output.getvalue().splitlines()
    assert sum("Episode" in line for line in lines) == blocks
    assert all(len(line) <= width for line in lines)


def test_id_cli_dispatch_and_json(monkeypatch, capsys):
    anilist = Mock()
    anilist.anime.return_value = AnimeMetadata(1, {"status": "FINISHED", "episodes": 2})
    subtitles = Mock()
    subtitles.anilist_files.return_value = KitsunekkoFileListResponse(1, (file("a", 1),))
    monkeypatch.setattr(series_cli, "HttpAniListSearchClient", lambda: anilist)
    monkeypatch.setattr(series_cli, "HttpKitsunekkoSubtitlesClient", lambda: subtitles)
    monkeypatch.setattr("sys.argv", ["ja-media", "subsync", "get-series", "--anilist", "1", "--format", "json"])
    main()
    result = json.loads(capsys.readouterr().out)
    assert result["covered"] == 1 and result["aired"] == 2
    anilist.search.assert_not_called()


def test_forced_search_uses_selected_metadata(monkeypatch, capsys):
    anilist = Mock()
    anilist.search.return_value = SearchResponse((SearchResult(
        1, None, None, "Title", None, None, "TV", 1.0,
        {"status": "FINISHED", "episodes": 2},
    ),))
    subtitles = Mock()
    subtitles.anilist_files.return_value = KitsunekkoFileListResponse(0, ())
    monkeypatch.setattr(series_cli, "HttpAniListSearchClient", lambda: anilist)
    monkeypatch.setattr(series_cli, "HttpKitsunekkoSubtitlesClient", lambda: subtitles)
    args = build_parser().parse_args(["subsync", "get-series", "Title", "--force-anilist", "--format", "json"])
    series_cli.run_get_series(args)
    assert json.loads(capsys.readouterr().out)["aired"] == 2
    assert anilist.search.call_args.kwargs["force_anilist"] is True
    anilist.anime.assert_not_called()


def test_ambiguous_search_requires_selection():
    results = SearchResponse(tuple(SearchResult(n, None, None, f"Title {n}", None, None, "TV", 1) for n in (1, 2)))
    with pytest.raises(ValueError, match="Several titles"):
        series_cli.select_candidate(results, Console(file=io.StringIO()), interactive=False)


def test_network_failure_is_not_empty_inventory(monkeypatch, capsys):
    client = Mock()
    client.anime.side_effect = httpx.ConnectError("unreachable")
    monkeypatch.setattr(series_cli, "HttpAniListSearchClient", lambda: client)
    args = build_parser().parse_args(["subsync", "get-series", "--anilist", "1"])
    with pytest.raises(SystemExit, match="Could not inspect series"):
        series_cli.run_get_series(args)
    assert not capsys.readouterr().out
