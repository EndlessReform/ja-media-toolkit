"""State contracts shared by the subsync Textual surface and its dialogs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


RemoteSourceKind = Literal["anilist", "tvdb"]


@dataclass
class RemoteLookupState:
    """Current Kitsunekko lookup selector visible in the TUI."""

    source: RemoteSourceKind | None = None
    external_id: int | None = None
    episode_number: int | None = None
    media_kind: str | None = "tv"
    status: str = ""


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
