"""Opt-in E2E harness for a real OpenAI-compatible model server.

Set ``JA_MEDIA_AGENT_E2E_BASE_URL`` and ``JA_MEDIA_AGENT_E2E_MODEL`` for a
manual run. The normal test suite skips this module's live test.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import json
import os

import pytest

from ja_media_core.anilist_search import AnimeMetadata, SearchResponse, SearchResult
from ja_media_data.operator.resolution_review import (
    ResolutionReviewSession,
    make_resolution_agent,
    make_review_context,
)
from ja_media_data.operator.resolution_review.provider import make_run_config
from ja_media_data.settings import AgentSettings
from dagster_test_support import review_documents
from operator_test_support import compile_campaign


BASE_URL = os.environ.get("JA_MEDIA_AGENT_E2E_BASE_URL")
MODEL_ID = os.environ.get("JA_MEDIA_AGENT_E2E_MODEL")


@dataclass
class FixtureAniList:
    """Deterministic metadata behind the real agent and real tool registry."""

    def search(self, _query: str, *, top_k: int, all_formats: bool, **_kwargs):
        assert top_k <= 10 and all_formats is True
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
        rows = {
            15451: {"title_romaji": "Example Season 1", "episodes": 12, "format": "TV"},
            200: {"title_romaji": "Other Show", "episodes": 12, "format": "TV"},
        }
        if anilist_id not in rows:
            raise KeyError(anilist_id)
        return AnimeMetadata(anilist_id=anilist_id, fields=rows[anilist_id])


@pytest.mark.skipif(
    not BASE_URL or not MODEL_ID,
    reason="set JA_MEDIA_AGENT_E2E_BASE_URL and JA_MEDIA_AGENT_E2E_MODEL",
)
@pytest.mark.asyncio
async def test_live_model_streams_proposal_and_resumes_after_approval(repository) -> None:
    campaign = compile_campaign(repository, source=review_documents())
    applied = []

    @contextmanager
    def repositories():
        yield repository

    def apply_in_memory(draft, _source_token):
        applied.append(draft)
        return {"status": "accepted_in_memory", "decisions": len(draft.decisions)}

    context = make_review_context(
        15451,
        repositories=repositories,
        bronze=campaign.store,
        anilist=FixtureAniList(),
        apply_draft=apply_in_memory,
    )
    session = ResolutionReviewSession(
        agent=make_resolution_agent(),
        context=context,
        run_config=make_run_config(
            AgentSettings(model_id=MODEL_ID, base_url=BASE_URL)
        ),
        max_turns=12,
    )

    before_approval = await _collect(session.start())
    approval = next(event for event in before_approval if event.kind == "approval_required")
    assert session.paused is True
    assert approval.data["proposal"]["rows"] == [
        {
            "capture_id": "capture-review",
            "filename": "Other_Show_Ep03_review.mkv",
            "current_anilist_id": 15451,
            "decision": "move_to_another_series",
            "destination_anilist_id": 200,
            "destination_episode": "3",
            "outcome": "canonical",
            "rationale": approval.data["proposal"]["rows"][0]["rationale"],
        }
    ]

    after_rejection = await _collect(
        session.reject("Name both the old and new AniList entries in the rationale.")
    )
    assert applied == []
    assert session.paused is True
    assert any(event.kind == "approval_required" for event in after_rejection)

    after_approval = await _collect(session.approve())
    assert session.paused is False
    assert applied and applied[0].current_anilist_id == 15451
    assert after_approval[-1].kind == "completed"


async def _collect(events):
    collected = []
    async for event in events:
        print(json.dumps({"kind": event.kind, **event.data}, ensure_ascii=False, default=str))
        collected.append(event)
    return collected
