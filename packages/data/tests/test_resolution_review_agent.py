"""Fast contract checks for the transport-neutral agent definition."""

import json
from types import SimpleNamespace

import pytest

from ja_media_data.operator.resolution_review.agent import (
    _finish_after_approved_proposal,
    initial_review_prompt,
    make_resolution_agent,
)
from ja_media_data.operator.resolution_review.models import FileEpisode
from ja_media_data.operator.resolution_review.streaming import (
    ResolutionReviewSession,
    _completed_event,
)


def test_agent_has_one_readable_static_registry() -> None:
    agent = make_resolution_agent()

    assert len(agent.tools) == 10
    assert agent.tools[-1].name == "propose_resolution_draft"
    assert agent.tools[-1].needs_approval is True
    assert agent.tool_use_behavior is _finish_after_approved_proposal
    comparison = next(
        tool for tool in agent.tools if tool.name == "compare_capture_to_kitsunekko"
    )
    assert comparison.strict_json_schema is False
    assert comparison.params_json_schema["required"] == [
        "capture_id",
        "proposed_anilist_id",
        "proposed_episode",
    ]


def test_initial_prompt_embeds_short_file_list() -> None:
    prompt = initial_review_prompt(
        15451,
        [
            {
                "issue_id": "issue-1",
                "capture_id": "capture-1",
                "filename": "Other_Show_Ep03.mkv",
                "reason": "title_mismatch",
            }
        ],
    )

    assert "Here are all current files" in prompt
    assert '"capture_id":"capture-1"' in prompt
    assert "use list_current_series_files" not in prompt


def test_long_list_prompt_requests_pagination() -> None:
    prompt = initial_review_prompt(15451, [{"capture_id": "capture-1"}], has_more=True)

    assert "first page" in prompt
    assert "offset 50" in prompt
    assert '"capture_id":"capture-1"' in prompt


def test_episode_is_integer_facing() -> None:
    assert FileEpisode(capture_id="capture-1", episode=3).episode == 3


@pytest.mark.asyncio
async def test_accept_approves_and_resumes_the_saved_run_state(monkeypatch) -> None:
    approved = []
    drained = []

    class State:
        def approve(self, interruption):
            approved.append(interruption)

    session = ResolutionReviewSession(
        agent=SimpleNamespace(),
        context=SimpleNamespace(),
        run_config=SimpleNamespace(),
        max_turns=1,
    )
    state = State()
    session._state = state
    interruption = SimpleNamespace(
        tool_name="propose_resolution_draft",
        raw_item={"call_id": "proposal-call"},
    )
    session._interruption = interruption

    async def drain(input_value, *, approved_resume=False):
        drained.append((input_value, approved_resume))
        yield SimpleNamespace(kind="completed", data={"result": {"status": "accepted"}})

    monkeypatch.setattr(session, "_drain", drain)

    events = [event async for event in session.approve()]

    assert approved == [interruption]
    assert drained == [(state, True)]
    assert [event.kind for event in events] == ["completed"]


def test_tool_behavior_stops_only_after_proposal_actually_runs() -> None:
    tool = SimpleNamespace(name="propose_resolution_draft")
    accepted = _finish_after_approved_proposal(
        SimpleNamespace(),
        [SimpleNamespace(tool=tool, output={"status": "accepted"})],
    )
    rejected = _finish_after_approved_proposal(
        SimpleNamespace(),
        [SimpleNamespace(tool=tool, output="The user rejected this proposal")],
    )

    assert accepted.is_final_output is True
    assert json.loads(accepted.final_output) == {"status": "accepted"}
    assert rejected.is_final_output is False
    assert rejected.final_output is None


def test_approved_tool_final_value_remains_structured_for_the_web_flow() -> None:
    event = _completed_event(
        '{"status":"accepted","durable_writeback":true}',
        approved_resume=True,
    )

    assert event.kind == "completed"
    assert event.data["result"] == {
        "status": "accepted",
        "durable_writeback": True,
    }
    assert "without another model turn" in event.data["output"]
