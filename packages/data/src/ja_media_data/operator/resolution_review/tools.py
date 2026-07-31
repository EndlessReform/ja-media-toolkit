"""The complete, static Agents SDK tool registry for one resolution review."""

from __future__ import annotations

from agents import RunContextWrapper, function_tool

from ja_media_data.operator.resolution_review.models import SeriesResolutionDraft
from ja_media_data.operator.resolution_review.toolbox import ReviewContext


@function_tool
def list_current_series_files(
    ctx: RunContextWrapper[ReviewContext], offset: int = 0, limit: int = 50
) -> list[dict]:
    """List rejected captures for the current Bronze-declared AniList series."""

    return ctx.context.toolbox.issues(
        ctx.context.current_anilist_id, offset, min(limit, 50)
    )


@function_tool
def get_resolution_issue(ctx: RunContextWrapper[ReviewContext], issue_id: str) -> dict:
    """Get one rejected capture and the automatic resolver's reason."""

    return ctx.context.toolbox.issue(ctx.context.current_anilist_id, issue_id)


@function_tool
def search_anilist(
    ctx: RunContextWrapper[ReviewContext], query: str, limit: int = 5
) -> list[dict]:
    """Search AniList titles, seasons, movies, OVAs, and specials."""

    return ctx.context.toolbox.search_anilist(query, min(limit, 10))


@function_tool
def get_anilist(ctx: RunContextWrapper[ReviewContext], anilist_id: int) -> dict:
    """Get titles, episode count, format, season, status, and relations for an ID."""

    return ctx.context.toolbox.get_anilist(anilist_id)


@function_tool
def list_capture_subtitles(
    ctx: RunContextWrapper[ReviewContext], capture_id: str
) -> list[dict]:
    """List only subtitle streams declared by this capture's pinned manifest."""

    return ctx.context.toolbox.list_subtitles(
        ctx.context.current_anilist_id, capture_id
    )


@function_tool(strict_mode=False)
def read_capture_subtitle(
    ctx: RunContextWrapper[ReviewContext],
    capture_id: str,
    stream_index: int,
    start_line: int = 1,
    line_count: int = 80,
) -> dict:
    """Read a bounded, numbered range from one declared subtitle stream.

    To continue, call again with start_line equal to returned next_start_line.
    Do not request the same range repeatedly.
    """

    text = ctx.context.toolbox.read_subtitle(
        ctx.context.current_anilist_id,
        capture_id,
        stream_index,
        start_line,
        min(line_count, 200),
    )
    returned = len(text.splitlines())
    return {
        "start_line": start_line,
        "line_count": returned,
        "text": text,
        "next_start_line": start_line + returned
        if returned == min(line_count, 200)
        else None,
    }


@function_tool(strict_mode=False)
def compare_capture_to_kitsunekko(
    ctx: RunContextWrapper[ReviewContext],
    capture_id: str,
    proposed_anilist_id: int,
    proposed_episode: int,
    page: int = 1,
    capture_stream_index: int | None = None,
    reference_subtitle_id: str | None = None,
) -> dict:
    """Compare a capture with one proposed Kitsunekko episode.

    Normally supply only capture_id, proposed_anilist_id, and proposed_episode. The
    tool automatically selects Japanese source/reference subtitles and returns 40
    lines from page 1. If inconclusive, call again with page equal to the returned
    next_page. Do not repeat the same page.
    """

    if page < 1:
        raise ValueError("comparison page must be >= 1")
    result = ctx.context.toolbox.compare_subtitles(
        ctx.context.current_anilist_id,
        capture_id,
        proposed_anilist_id,
        proposed_episode,
        capture_stream_index=capture_stream_index,
        reference_subtitle_id=reference_subtitle_id,
        start_line=(page - 1) * 40 + 1,
        line_count=40,
    )
    result["page"] = page
    result["next_page"] = page + 1 if result.get("next_start_line") else None
    return result


@function_tool
def save_resolution_draft(
    ctx: RunContextWrapper[ReviewContext], draft: SeriesResolutionDraft
) -> dict:
    """Replace the current draft and return its validated crosswalk preview.

    The draft is one object, not a list. It contains current_anilist_id,
    summary, and decisions. Each decision is flat; a move contains decision,
    destination_anilist_id, files of {capture_id, episode}, and rationale.
    """

    return ctx.context.toolbox.save_draft(ctx.context.current_anilist_id, draft)


@function_tool
def preview_resolution_draft(ctx: RunContextWrapper[ReviewContext]) -> dict:
    """Return every saved capture's final series, episode, and outcome."""

    return ctx.context.toolbox.preview_draft()


@function_tool(needs_approval=True)
def propose_resolution_draft(ctx: RunContextWrapper[ReviewContext]) -> dict:
    """Apply the last validated complete draft after explicit human approval."""

    return ctx.context.toolbox.apply_approved_draft()


CORE_TOOLS = (
    list_current_series_files,
    get_resolution_issue,
    search_anilist,
    get_anilist,
    list_capture_subtitles,
    read_capture_subtitle,
    compare_capture_to_kitsunekko,
    save_resolution_draft,
    preview_resolution_draft,
    propose_resolution_draft,
)
