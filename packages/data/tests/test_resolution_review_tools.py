"""Integration harness for the transport-neutral Agents SDK tool registry."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import json

import pytest
from agents.tool_context import ToolContext

from ja_media_core.anilist_search import (
    AnimeMetadata,
    SearchResponse,
    SearchResult,
)
from ja_media_core.kitsunekko import KitsunekkoFileListResponse
from ja_media_data.operator.resolution_review.agent import INSTRUCTIONS
from ja_media_data.operator.resolution_review.anilist_metadata import (
    MAX_SYNOPSIS_CHARS,
    synopsis_text,
)
from ja_media_data.operator.resolution_review.factory import make_review_context
from ja_media_data.operator.resolution_review.models import SeriesResolutionDraft
from ja_media_data.operator.resolution_review.toolbox import ReviewContext
from ja_media_data.operator.resolution_review.tools import CORE_TOOLS
from dagster_test_support import review_documents
from operator_test_support import compile_campaign


@dataclass
class FakeAniList:
    """Stable service-SDK stand-in; the toolbox still uses the public contract."""

    def search(self, query: str, *, top_k: int, all_formats: bool, **_kwargs):
        assert query == "Other Show"
        assert top_k == 5 and all_formats is True
        return SearchResponse(
            results=(
                SearchResult(
                    anilist_id=200,
                    title_english="Other Show",
                    title_native="別の番組",
                    title_romaji="Other Show",
                    season="SPRING",
                    season_year=2026,
                    format="TV",
                    score=12.0,
                ),
            )
        )

    def anime(self, anilist_id: int, *, fields=None):
        assert fields is not None
        assert "description" in fields
        rows = {
            15451: {"title_romaji": "Example", "episodes": 12, "format": "TV"},
            200: {
                "title_romaji": "Other Show",
                "episodes": 12,
                "format": "TV",
                "description": "A different <b>show</b>.<br>With context &amp; clues.",
            },
        }
        if anilist_id not in rows:
            raise KeyError(anilist_id)
        return AnimeMetadata(anilist_id=anilist_id, fields=rows[anilist_id])


class FakeKitsunekko:
    """One deterministic episode reference through the real SDK contract."""

    def anilist_episode_files(self, anilist_id: int, episode_number: int):
        if (anilist_id, episode_number) == (201, 1):
            return KitsunekkoFileListResponse(count=0, files=())
        assert (anilist_id, episode_number) == (200, 3)
        return KitsunekkoFileListResponse(
            count=1,
            files=(
                {
                    "subtitle_id": "reference-200-3",
                    "filename": "Other Show - 03.ssa",
                    "extension": "ssa",
                },
            ),
        )

    def anilist_files(self, anilist_id: int):
        assert anilist_id == 201
        return KitsunekkoFileListResponse(count=0, files=(), anilist_id=201)

    def file_content(self, file_ref: str) -> bytes:
        assert file_ref == "reference-200-3"
        lines = ["1", "00:00:00,000 --> 00:00:01,000", "これは日本語です。"]
        lines.extend(f"reference line {number}" for number in range(4, 46))
        return "\n".join(lines).encode()


@pytest.mark.asyncio
async def test_registry_tools_against_a_real_compiled_campaign(repository) -> None:
    campaign = compile_campaign(repository, source=review_documents())

    @contextmanager
    def repositories():
        yield repository

    context = make_review_context(
        15451,
        repositories=repositories,
        bronze=campaign.store,
        anilist=FakeAniList(),
        kitsunekko=FakeKitsunekko(),
    )
    toolbox = context.toolbox

    listed = await _invoke("list_current_series_files", context, {})
    assert len(listed) == 1
    issue = listed[0]
    assert issue["capture_id"] == "capture-review"
    assert issue["reason"] == "filename_title_not_equal_to_declared_anilist_titles"

    selected = await _invoke(
        "get_resolution_issue", context, {"issue_id": issue["issue_id"]}
    )
    assert selected["source_hint"].endswith("review.mkv")

    search = await _invoke("search_anilist", context, {"query": "Other Show"})
    assert search[0]["anilist_id"] == 200
    metadata = await _invoke("get_anilist", context, {"anilist_id": 200})
    assert metadata["episodes"] == 12
    assert metadata["description_text"] == "A different show.\nWith context & clues."
    assert "description" not in metadata

    subtitles = await _invoke(
        "list_capture_subtitles", context, {"capture_id": "capture-review"}
    )
    assert subtitles[0]["stream_index"] == 2
    excerpt = await _invoke(
        "read_capture_subtitle",
        context,
        {"capture_id": "capture-review", "stream_index": 2, "line_count": 3},
    )
    assert "3: これは日本語です。" in excerpt["text"]
    assert excerpt["next_start_line"] == 4
    next_excerpt = await _invoke(
        "read_capture_subtitle",
        context,
        {
            "capture_id": "capture-review",
            "stream_index": 2,
            "start_line": excerpt["next_start_line"],
            "line_count": 3,
        },
    )
    assert next_excerpt["start_line"] == 4

    draft_payload = {
        "current_anilist_id": 15451,
        "summary": "The rejected file belongs to Other Show.",
        "decisions": [
            {
                "decision": "move_to_another_series",
                "destination_anilist_id": 200,
                "files": [{"capture_id": "capture-review", "episode": 3}],
                "rationale": "The filename identifies the other series.",
            }
        ],
    }
    unchecked = toolbox.save_draft(
        15451, SeriesResolutionDraft.model_validate(draft_payload)
    )
    assert unchecked["rows"][0]["subtitle_comparison"] == "not_checked"

    comparison = await _invoke(
        "compare_capture_to_kitsunekko",
        context,
        {
            "capture_id": "capture-review",
            "proposed_anilist_id": 200,
            "proposed_episode": 3,
        },
    )
    assert comparison["status"] == "compared"
    assert "これは日本語です。" in comparison["reference"]["text"]
    assert comparison["page"] == 1
    assert comparison["next_page"] == 2
    comparison_page_2 = await _invoke(
        "compare_capture_to_kitsunekko",
        context,
        {
            "capture_id": "capture-review",
            "proposed_anilist_id": 200,
            "proposed_episode": 3,
            "page": comparison["next_page"],
        },
    )
    assert comparison_page_2["page"] == 2
    assert "41: reference line 41" in comparison_page_2["reference"]["text"]
    missing = await _invoke(
        "compare_capture_to_kitsunekko",
        context,
        {
            "capture_id": "capture-review",
            "proposed_anilist_id": 201,
            "proposed_episode": 1,
        },
    )
    assert missing["availability"] == "series_not_indexed"

    preview = await _invoke(
        "save_resolution_draft",
        context,
        {"draft": draft_payload},
    )
    assert preview["rows"][0]["destination_anilist_id"] == 200
    assert preview["rows"][0]["subtitle_comparison"] == "compared"
    assert await _invoke("preview_resolution_draft", context, {}) == preview

    proposal = _tool("propose_resolution_draft")
    assert proposal.needs_approval is True
    with pytest.raises(RuntimeError, match="writeback is not installed"):
        toolbox.apply_approved_draft()


async def _invoke(name: str, context: ReviewContext, arguments: dict):
    """Call the SDK-generated JSON boundary exactly as Runner will."""

    payload = json.dumps(arguments)
    tool = _tool(name)
    result = await tool.on_invoke_tool(
        ToolContext(
            context=context,
            tool_name=name,
            tool_call_id=f"manual-{name}",
            tool_arguments=payload,
        ),
        payload,
    )
    if isinstance(result, str) and result[:1] in {"{", "[", '"'}:
        return json.loads(result)
    return result


def _tool(name: str):
    return next(tool for tool in CORE_TOOLS if tool.name == name)


def test_agent_instructions_define_every_draft_variant_and_locator_policy() -> None:
    assert '"decision":"keep_in_current_series"' in INSTRUCTIONS
    assert '"capture_ids":["capture-3"]' in INSTRUCTIONS
    assert "exactly one capture" in INSTRUCTIONS
    assert "do not infer a preferred" in INSTRUCTIONS


def test_synopsis_text_is_plain_and_bounded() -> None:
    assert synopsis_text("<p>First &amp; second</p><p>Third</p>") == (
        "First & second\n\nThird"
    )
    bounded = synopsis_text("x" * (MAX_SYNOPSIS_CHARS + 20))
    assert bounded is not None
    assert len(bounded) == MAX_SYNOPSIS_CHARS
    assert bounded.endswith("…")
