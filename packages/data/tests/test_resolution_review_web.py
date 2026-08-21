"""HTTP adapter tests over a scripted transport-neutral review session."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient

from ja_media_data.operator.http.app import create_operator_app
from ja_media_data.operator.resolution_review.catalog import (
    ReviewSeries,
    ReviewSeriesPage,
)
from ja_media_data.operator.resolution_review.streaming import ReviewEvent


PROPOSAL = {
    "summary": "Move the misplaced episode.",
    "rows": [
        {
            "capture_id": "capture-review",
            "filename": "Other_Show_Ep03.mkv",
            "current_anilist_id": 15451,
            "current_anilist": {
                "anilist_id": 15451,
                "title": "Example Season 1",
                "tooltip": "AniList 15451 · Example Season 1 · TV · 12 episodes",
                "url": "https://anilist.co/anime/15451",
            },
            "decision": "move_to_another_series",
            "destination_anilist_id": 200,
            "proposed_anilist": {
                "anilist_id": 200,
                "title": "Other Show",
                "tooltip": "AniList 200 · Other Show · TV · 12 episodes",
                "url": "https://anilist.co/anime/200",
            },
            "destination_episode": "3",
            "subtitle_comparison": "compared",
            "outcome": "canonical",
            "rationale": "The filename identifies Other Show.",
        }
    ],
}


class ScriptedSession:
    async def start(self):
        yield ReviewEvent(
            "files_loaded",
            {"files": [{"capture_id": "capture-review"}], "needs_pagination": False},
        )
        yield ReviewEvent(
            "tool_output",
            {
                "tool_name": "compare_capture_to_kitsunekko",
                "output": {
                    "status": "compared",
                    "capture": {
                        "capture_id": "capture-review",
                        "filename": "Other_Show_Ep03.ass",
                        "text": "1: source dialogue",
                    },
                    "reference": {
                        "anilist_id": 200,
                        "episode": "3",
                        "subtitle_id": "reference-3",
                        "filename": "Other Show - 03.srt",
                        "text": "1: reference dialogue",
                    },
                    "reference_candidates": [
                        {
                            "subtitle_id": "reference-3",
                            "filename": "Other Show - 03.srt",
                        }
                    ],
                    "next_start_line": 41,
                },
            },
        )
        yield ReviewEvent("approval_required", {"proposal": PROPOSAL})

    async def reject(self, reason: str):
        assert reason == "Name both entries."
        revised = {**PROPOSAL, "summary": "Revised after rejection."}
        yield ReviewEvent("approval_required", {"proposal": revised})

    async def approve(self):
        yield ReviewEvent(
            "tool_output",
            {
                "tool_name": "propose_resolution_draft",
                "call_id": "call-1",
                "output": {"durable_writeback": False},
            },
        )
        yield ReviewEvent("completed", {"output": "Accepted locally."})


class ScriptedRuntime:
    def __init__(self) -> None:
        self.paused = {}
        self.counter = 0
        self.last_choice = None
        self.reversed = []

    @property
    def review_mode(self):
        return "dev"

    def review_series(self, *, view: str = "pending", offset: int, limit: int):
        items = (
            ()
            if view == "resolved" and self.reversed
            else (ReviewSeries(15451, "Example Season 1", 1),)
        )
        return ReviewSeriesPage(
            items=items,
            total=len(items),
            offset=offset,
            limit=limit,
        )

    def active_resolutions(self, anilist_id: int):
        if self.reversed:
            return ()
        item = SimpleNamespace(
            capture_id="capture-review",
            decision="move_to_another_series",
            destination_anilist_id=200,
            destination_episode="3",
            rationale="The filename identifies Other Show.",
        )
        return (
            SimpleNamespace(
                batch_id="resolution-batch-1",
                summary="Move the misplaced episode.",
                created_at=datetime(2026, 7, 31, tzinfo=UTC),
                items=(item,),
            ),
        )

    def reverse_resolution(self, batch_id: str, *, reason: str):
        assert batch_id == "resolution-batch-1"
        assert reason == "Wrong destination"
        self.reversed.append(batch_id)
        return {"status": "reversed"}

    def review_context(self, anilist_id: int):
        assert anilist_id == 15451
        issue = {
            "capture_id": "capture-review",
            "source_hint": "Other_Show_Ep03.mkv",
            "kind": "invalid",
            "reason": "filename_title_not_equal_to_declared_anilist_titles",
        }
        toolbox = SimpleNamespace(issues=lambda *_args: [issue])
        return SimpleNamespace(toolbox=toolbox)

    def start_review(self, anilist_id: int, choice):
        assert anilist_id == 15451
        self.last_choice = choice
        return ScriptedSession()

    def pause_review(self, session) -> str:
        self.counter += 1
        token = f"review-token-{self.counter}"
        self.paused[token] = session
        return token

    def take_paused_review(self, token: str):
        try:
            return self.paused.pop(token)
        except KeyError as error:
            raise KeyError("review approval expired or was already used") from error

    def close(self) -> None:
        self.paused.clear()


def test_page_and_post_sse_wrap_the_same_session() -> None:
    runtime = ScriptedRuntime()
    app = create_operator_app(runtime_factory=lambda: runtime)

    with TestClient(app) as client:
        page = client.get("/operator/resolution-review")
        started = client.post(
            "/operator/resolution-review/series/15451/run",
            data={
                "model_id": "test-model",
                "base_url": "http://model.test/v1",
                "max_turns": "17",
            },
        )
        rejected = client.post(
            "/operator/resolution-review/paused/review-token-1/reject",
            data={"reason": "Name both entries."},
        )
        accepted = client.post(
            "/operator/resolution-review/paused/review-token-2/accept"
        )

    assert page.status_code == 200
    assert "Example Season 1" in page.text
    assert "Other_Show_Ep03.mkv" in page.text
    assert '"defaultTimeout":0' in page.text
    assert "/vendor/hx-sse.min.js" in page.text
    assert started.headers["content-type"].startswith("text/event-stream")
    assert "Files loaded" in started.text
    assert "Proposed Crosswalk" in started.text
    assert "Subtitle comparison" in started.text
    assert "source dialogue" in started.text
    assert "reference dialogue" in started.text
    assert "COMPARED" in started.text
    assert 'href="https://anilist.co/anime/15451"' in started.text
    assert 'href="https://anilist.co/anime/200"' in started.text
    assert 'target="_blank"' in started.text
    assert "data-proposal-expand" in started.text
    assert '<dialog class="proposal-dialog"' in started.text
    assert "review-token-1" in started.text
    assert runtime.last_choice.model_id == "test-model"
    assert runtime.last_choice.max_turns == 17
    assert 'name="max_turns"' in page.text
    assert "Revised after rejection" in rejected.text
    assert "review-token-2" in rejected.text
    assert "durable_writeback" in accepted.text
    assert "No durable control rows were written" in accepted.text


def test_used_or_unknown_pause_token_returns_streamed_error() -> None:
    runtime = ScriptedRuntime()
    app = create_operator_app(runtime_factory=lambda: runtime)

    with TestClient(app) as client:
        response = client.post("/operator/resolution-review/paused/nope/accept")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "already used" in response.text


def test_max_turns_override_is_bounded_before_starting_a_run() -> None:
    runtime = ScriptedRuntime()
    app = create_operator_app(runtime_factory=lambda: runtime)

    with TestClient(app) as client:
        response = client.post(
            "/operator/resolution-review/series/15451/run",
            data={"max_turns": "31"},
        )

    assert response.status_code == 422
    assert runtime.last_choice is None


def test_resolved_rail_exposes_applied_crosswalk_and_reversal() -> None:
    runtime = ScriptedRuntime()
    app = create_operator_app(runtime_factory=lambda: runtime)

    with TestClient(app) as client:
        page = client.get(
            "/operator/resolution-review",
            params={"view": "resolved", "anilist_id": 15451},
        )
        reversed_response = client.post(
            "/operator/resolution-review/history/resolution-batch-1/reverse",
            data={
                "series_view": "resolved",
                "anilist_id": "15451",
                "reason": "Wrong destination",
            },
        )

    assert page.status_code == 200
    assert "Pending · 1" in page.text
    assert "Resolved · 1" in page.text
    assert "Applied Crosswalks" in page.text
    assert "capture-review" in page.text
    assert "REVERSE BATCH" in page.text
    assert reversed_response.status_code == 200
    assert "No resolved series" in reversed_response.text
    assert "no active accepted decisions" in reversed_response.text
