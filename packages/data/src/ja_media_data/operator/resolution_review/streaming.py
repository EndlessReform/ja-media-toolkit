"""Transport-neutral semantic streaming and HITL resume for one review."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Literal

from agents import Agent, ItemHelpers, RunConfig, Runner, RunState, ToolApprovalItem
from agents.items import (
    MessageOutputItem,
    ReasoningItem,
    ToolCallItem,
    ToolCallOutputItem,
)
from agents.stream_events import StreamEvent
from pydantic import BaseModel

from ja_media_data.operator.resolution_review.agent import initial_review_prompt
from ja_media_data.operator.resolution_review.toolbox import ReviewContext


ReviewEventKind = Literal[
    "files_loaded",
    "agent_started",
    "reasoning",
    "tool_called",
    "tool_output",
    "message",
    "approval_required",
    "completed",
]


@dataclass(frozen=True)
class ReviewEvent:
    """One small event suitable for a terminal, SSE adapter, or test harness."""

    kind: ReviewEventKind
    data: dict[str, Any]


class ResolutionReviewSession:
    """Own one live SDK run and its process-local approval pause."""

    def __init__(
        self,
        *,
        agent: Agent[ReviewContext],
        context: ReviewContext,
        run_config: RunConfig,
        max_turns: int,
    ) -> None:
        self.agent = agent
        self.context = context
        self.run_config = run_config
        self.max_turns = max_turns
        self._started = False
        self._state: RunState[ReviewContext] | None = None
        self._interruption: ToolApprovalItem | None = None
        self._tool_names: dict[str, str] = {}

    @property
    def paused(self) -> bool:
        """Whether the proposal tool is waiting for a human decision."""

        return self._state is not None

    async def start(self, prompt: str | None = None) -> AsyncIterator[ReviewEvent]:
        """Start once and stream until completion or proposal approval is required."""

        if self._started:
            raise RuntimeError("review session has already started")
        self._started = True
        files = self.context.toolbox.issues(self.context.current_anilist_id, 0, 50)
        has_more = len(files) == 50 and bool(
            self.context.toolbox.issues(self.context.current_anilist_id, 50, 1)
        )
        compact = [
            {
                "issue_id": item["issue_id"],
                "capture_id": item["capture_id"],
                "filename": item["source_hint"],
                "reason": item["reason"],
            }
            for item in files
        ]
        yield ReviewEvent(
            "files_loaded",
            {"files": compact, "needs_pagination": has_more},
        )
        review_prompt = initial_review_prompt(
            self.context.current_anilist_id,
            compact,
            has_more=has_more,
        )
        if prompt:
            review_prompt += "\nAdditional user instruction: " + prompt.strip()
        async for event in self._drain(review_prompt):
            yield event

    async def approve(self) -> AsyncIterator[ReviewEvent]:
        """Approve the pending SDK tool call and resume it to deterministic completion."""

        state, interruption = self._take_pause()
        state.approve(interruption)
        async for event in self._drain(state, approved_resume=True):
            yield event

    async def reject(self, reason: str) -> AsyncIterator[ReviewEvent]:
        """Reject the pending proposal with a model-visible reason and resume."""

        reason = reason.strip()
        if not reason:
            raise ValueError("a rejection reason is required")
        state, interruption = self._take_pause()
        state.reject(
            interruption,
            rejection_message=(
                f"The user rejected this proposal: {reason} "
                "Revise the draft, save it, preview it, and propose it again."
            ),
        )
        async for event in self._drain(state):
            yield event

    def _take_pause(self) -> tuple[RunState[ReviewContext], ToolApprovalItem]:
        if self._state is None or self._interruption is None:
            raise RuntimeError("review session is not waiting for approval")
        state, interruption = self._state, self._interruption
        self._state = None
        self._interruption = None
        return state, interruption

    async def _drain(
        self,
        input_value: str | RunState[ReviewContext],
        *,
        approved_resume: bool = False,
    ) -> AsyncIterator[ReviewEvent]:
        context = self.context if isinstance(input_value, str) else None
        result = Runner.run_streamed(
            self.agent,
            input_value,
            context=context,
            run_config=self.run_config,
            max_turns=self.max_turns,
        )
        async for sdk_event in result.stream_events():
            event = _semantic_event(sdk_event, self._tool_names)
            if event is not None:
                yield event

        if result.interruptions:
            if len(result.interruptions) != 1:
                raise RuntimeError("a review run may pause on only one proposal")
            interruption = result.interruptions[0]
            if interruption.tool_name != "propose_resolution_draft":
                raise RuntimeError(
                    f"unexpected approval tool: {interruption.tool_name}"
                )
            self._state = result.to_state()
            self._interruption = interruption
            yield ReviewEvent(
                "approval_required",
                {
                    "tool_name": interruption.tool_name,
                    "arguments": _arguments(interruption.raw_item),
                    "proposal": self.context.toolbox.preview_draft(),
                },
            )
            return

        yield _completed_event(result.final_output, approved_resume=approved_resume)


def _semantic_event(
    event: StreamEvent, tool_names: dict[str, str]
) -> ReviewEvent | None:
    if event.type == "agent_updated_stream_event":
        return ReviewEvent("agent_started", {"name": event.new_agent.name})
    if event.type != "run_item_stream_event":
        return None
    item = event.item
    if event.name == "reasoning_item_created" and isinstance(item, ReasoningItem):
        summary = "\n".join(
            part.text for part in (item.raw_item.summary or []) if part.text
        )
        return ReviewEvent("reasoning", {"summary": summary}) if summary else None
    if event.name == "tool_called" and isinstance(item, ToolCallItem):
        name = item.tool_name or "unknown_tool"
        if item.call_id:
            tool_names[item.call_id] = name
        return ReviewEvent(
            "tool_called",
            {
                "tool_name": name,
                "call_id": item.call_id,
                "arguments": _arguments(item.raw_item),
            },
        )
    if event.name == "tool_output" and isinstance(item, ToolCallOutputItem):
        tool_name = tool_names.get(item.call_id or "", "unknown_tool")
        return ReviewEvent(
            "tool_output",
            {
                "tool_name": tool_name,
                "call_id": item.call_id,
                "output": _bounded_output(
                    _decoded_tool_output(item.output),
                    limit=20_000
                    if tool_name == "compare_capture_to_kitsunekko"
                    else 4_000,
                ),
            },
        )
    if event.name == "message_output_created" and isinstance(item, MessageOutputItem):
        return ReviewEvent("message", {"text": ItemHelpers.text_message_output(item)})
    return None


def _arguments(raw_item: Any) -> Any:
    value = (
        raw_item.get("arguments")
        if isinstance(raw_item, dict)
        else getattr(raw_item, "arguments", None)
    )
    if not isinstance(value, str):
        return _json_value(value)
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _call_id(raw_item: Any) -> str | None:
    value = (
        raw_item.get("call_id")
        if isinstance(raw_item, dict)
        else getattr(raw_item, "call_id", None)
    )
    return str(value) if value else None


def _bounded_output(value: Any, limit: int = 4_000) -> Any:
    normalized = _json_value(value)
    rendered = json.dumps(normalized, ensure_ascii=False, default=str)
    if len(rendered) <= limit:
        return normalized
    return {"preview": rendered[:limit], "truncated": True}


def _decoded_tool_output(value: Any) -> Any:
    """Recover structured function-tool JSON for semantic HTTP rendering."""

    if not isinstance(value, str) or value[:1] not in {"{", "["}:
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _completed_event(value: Any, *, approved_resume: bool) -> ReviewEvent:
    """Expose an approved tool's JSON final value to the HTTP acceptance path."""

    output = _decoded_tool_output(value)
    if approved_resume and isinstance(output, dict):
        return ReviewEvent(
            "completed",
            {
                "output": "Approved draft applied without another model turn.",
                "result": output,
            },
        )
    return ReviewEvent("completed", {"output": _json_value(output)})


def _json_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if value is None or isinstance(value, (str, int, float, bool, list, dict)):
        return value
    return str(value)
