"""The single agent used to investigate and propose a series crosswalk."""

from __future__ import annotations

import json

from agents import (
    Agent,
    FunctionToolResult,
    RunContextWrapper,
    ToolsToFinalOutputResult,
)

from ja_media_data.operator.resolution_review.toolbox import ReviewContext
from ja_media_data.operator.resolution_review.tools import CORE_TOOLS


INSTRUCTIONS = """You resolve rejected anime episode bindings for one AniList series.

Work through the supplied tools; never invent capture IDs, episode numbers, or AniList IDs.
The user message normally includes the current rejected files. Call the list tool only
when that list says it was omitted because it is long. For every file you intend to
include in the draft, inspect the rejection, search AniList when the filename suggests
another series, and check exact metadata. Use compare_capture_to_kitsunekko when actual
dialogue would materially distinguish a proposed destination. For a homogeneous batch,
checking a small representative sample is enough; do not repeat unavailable lookups or
make ritual calls for every file. If an excerpt is inconclusive, advance with the tool's
returned next_start_line or next_page; never request the same range again unless changing
the subtitle stream/reference. A missing or unreachable reference is not confirmation.

Save one series-level draft containing all decisions you are proposing. Use:
- keep_in_current_series when the capture belongs to the current AniList entry;
- move_to_another_series when it belongs to a different AniList entry; or
- leave_out_of_episode_index for extras that should not become ordinary episodes.

The draft is one object, not a list. Its exact shape is:
{"current_anilist_id":15451, "summary":"what changes", "decisions":[
 {"decision":"move_to_another_series", "destination_anilist_id":200,
  "files":[{"capture_id":"capture-1", "episode":3}], "rationale":"why"}]}

Preview the saved draft, then call propose_resolution_draft once for that draft. That call
pauses for human review. If the user rejects it, revise, save, preview, and propose again.
Do not claim that anything was applied before the tool returns.
"""


def make_resolution_agent() -> Agent[ReviewContext]:
    """Build the readable, static agent definition shared by every adapter."""

    return Agent[ReviewContext](
        name="Episode resolution reviewer",
        instructions=INSTRUCTIONS,
        tools=list(CORE_TOOLS),
        tool_use_behavior=_finish_after_approved_proposal,
    )


def _finish_after_approved_proposal(
    _context: RunContextWrapper[ReviewContext],
    results: list[FunctionToolResult],
) -> ToolsToFinalOutputResult:
    """Make a successful approved proposal the run's deterministic final value.

    The SDK's HITL guide resumes an interrupted call by recording the decision
    with ``RunState.approve()`` or ``reject()`` and passing that state back to
    ``Runner.run_streamed()``. Its agent guide then assigns the decision about
    whether a completed function tool ends the run to ``tool_use_behavior``:

    * https://openai.github.io/openai-agents-python/human_in_the_loop/
    * https://openai.github.io/openai-agents-python/agents/#tool-use-behavior

    The same contracts can be inspected in the review-only checkout under
    ``docs/repo-symlinks``: ``openai-agents-python/src/agents/run_state.py``
    records approval, ``agent.py`` defines ``ToolsToFinalOutputFunction``, and
    ``run_internal/turn_resolution.py`` checks its result before asking the
    model for another turn. Runtime installation comes from the released PyPI
    wheel, not that checkout.

    ``StopAtTools`` is deliberately not used. The SDK represents a rejected
    function call as a string tool result too, so stopping solely by tool name
    would also terminate rejection. Our function tool is annotated to return a
    dict; therefore a dict from this one tool means it actually ran after
    approval, while the rejection string remains model-visible for revision.
    JSON keeps that structured value intact through the agent's default string
    final-output contract.
    """

    for result in results:
        if result.tool.name == "propose_resolution_draft" and isinstance(
            result.output, dict
        ):
            return ToolsToFinalOutputResult(
                is_final_output=True,
                final_output=json.dumps(
                    result.output, ensure_ascii=False, separators=(",", ":")
                ),
            )
    return ToolsToFinalOutputResult(is_final_output=False, final_output=None)


def initial_review_prompt(
    current_anilist_id: int,
    initial_files: list[dict],
    *,
    has_more: bool = False,
) -> str:
    """Ask for one complete review without duplicating the system instructions."""

    request = (
        f"Review the rejected captures currently declared as AniList {current_anilist_id}. "
        "Investigate them, save and preview one draft, then propose it for approval."
    )
    scope = (
        " Here is the first page (offset 0, limit 50). Review it first, then use "
        "list_current_series_files with offset 50 to continue.\n"
        if has_more
        else " Here are all current files:\n"
    )
    return (
        request
        + scope
        + json.dumps(initial_files, ensure_ascii=False, separators=(",", ":"))
    )
