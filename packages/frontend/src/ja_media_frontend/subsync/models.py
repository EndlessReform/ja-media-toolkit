"""State contracts shared by the subsync Textual surface and its dialogs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from ja_media_core.subsync import infer_anilist_id, infer_episode_number


RemoteSourceKind = Literal["anilist", "tvdb"]


@dataclass
class RemoteLookupState:
    """Current Kitsunekko lookup selector visible in the TUI."""

    source: RemoteSourceKind | None = None
    external_id: int | None = None
    episode_number: int | None = None
    media_kind: str | None = "tv"
    status: str = ""


def initial_remote_lookup_state(
    media_path: Path,
    *,
    anilist_id: int | None,
    tvdb_id: int | None,
    episode_number: int | None,
    tvdb_media_kind: str | None,
) -> RemoteLookupState:
    """Build initial lookup state, preferring explicit IDs over path inference."""

    if anilist_id is not None and tvdb_id is not None:
        raise ValueError("Pass only one of --anilist or --tvdb")
    inferred_id = (
        infer_anilist_id(media_path)
        if anilist_id is None and tvdb_id is None
        else None
    )
    selected_anilist_id = anilist_id or inferred_id
    return RemoteLookupState(
        source=(
            "anilist"
            if selected_anilist_id is not None
            else "tvdb"
            if tvdb_id is not None
            else None
        ),
        external_id=selected_anilist_id or tvdb_id,
        episode_number=(
            episode_number
            if episode_number is not None
            else infer_episode_number(media_path.stem)
        ),
        media_kind=tvdb_media_kind,
    )


@dataclass(frozen=True)
class RemoteLookupRequest:
    """Values submitted from the in-flight Kitsunekko lookup dialog."""

    source: RemoteSourceKind
    external_id: int
    episode_number: int
    media_kind: str | None = "tv"


@dataclass(frozen=True)
class ManualSubtitlePickRequest:
    """Remote subtitle row chosen from the full series inventory."""

    file: dict[str, Any]
