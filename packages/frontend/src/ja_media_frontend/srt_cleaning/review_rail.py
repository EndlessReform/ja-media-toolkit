from __future__ import annotations

from textual.widgets import Label, ListItem, ListView

from ja_media_frontend.srt_cleaning.review_models import ReviewWorkspace


class ReviewEpisodeRail(ListView):
    """Visible navigation across every series/episode pair in a cleaning run."""

    def __init__(
        self,
        workspace: ReviewWorkspace,
        *,
        initial_index: int,
        id: str,
    ) -> None:
        items = [
            ListItem(Label(_episode_label(workspace, key)), id=f"episode-{index}")
            for index, key in enumerate(workspace.episode_keys)
        ]
        super().__init__(*items, initial_index=initial_index, id=id)


def _episode_label(workspace: ReviewWorkspace, key: tuple[int, int]) -> str:
    anilist_id, episode = key
    sources = tuple(
        source
        for source in workspace.sources
        if (source.anilist_id, source.episode_number) == key
    )
    changed = sum(source.changed_count for source in sources)
    source_label = "src" if len(sources) == 1 else "srcs"
    return f"{anilist_id}:{episode:02d}  {len(sources)} {source_label}  {changed} changed"
