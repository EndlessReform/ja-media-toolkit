"""Remote subtitle actions mixed into the Textual subsync application."""

from __future__ import annotations

import re
from typing import Any

from ja_media_core.kitsunekko import (
    HttpKitsunekkoSubtitlesClient,
    KitsunekkoFileListResponse,
)
from ja_media_frontend.subsync.dialogs import (
    HelpModal,
    RemoteFilePickModal,
    RemoteLookupModal,
)
from ja_media_frontend.subsync.models import (
    ManualSubtitlePickRequest,
    RemoteLookupRequest,
    RemoteLookupState,
)
from ja_media_frontend.subsync.service import (
    SubtitleLookup,
    SubtitleTrack,
    fetch_episode_files,
    fetch_series_files,
    is_supported_remote_subtitle,
    materialize_remote_track,
    sidecar_path,
)


class SubsyncRemoteMixin:
    """Kitsunekko lookup, download, and picker behavior."""

    def action_open_remote_lookup(self) -> None:
        self.push_screen(RemoteLookupModal(self.remote_state), self.apply_remote_lookup)

    def action_open_help(self) -> None:
        self.push_screen(HelpModal())

    def action_open_remote_file_picker(self) -> None:
        self._open_remote_file_picker("Select one subtitle from the full series list.")

    def apply_remote_lookup(self, request: RemoteLookupRequest | None) -> None:
        if request is None:
            return
        self.remote_state = RemoteLookupState(
            source=request.source,
            external_id=request.external_id,
            episode_number=request.episode_number,
            media_kind=request.media_kind,
            status="fetching...",
        )
        self.refresh_view()
        try:
            added_count, first_idx = self.fetch_remote_tracks()
        except Exception as exc:  # pragma: no cover - transport details vary.
            if self._should_offer_manual_pick(exc):
                self.remote_state.status = "episode not found; pick manually"
                self.notify(
                    "Episode lookup found no match. Opening full-series picker.",
                    severity="warning",
                )
                self._open_remote_file_picker(
                    "Episode lookup returned 404; choose from the full series list."
                )
            else:
                self.remote_state.status = f"fetch failed: {exc}"
                self.notify(str(exc), severity="error")
        else:
            self.remote_state.status = f"fetched {added_count} track(s)"
            if added_count:
                self.track_index = first_idx
                self.select_cue_near_time(self.window_start_s + self.window_s / 2)
        self.refresh_view()

    def fetch_remote_tracks_or_exit(self) -> None:
        try:
            added_count, _ = self.fetch_remote_tracks()
        except Exception as exc:
            if not self._should_offer_manual_pick(exc):
                raise SystemExit(
                    f"Could not fetch Kitsunekko subtitles: {exc}"
                ) from exc
            self.remote_state.status = "episode not found; pick manually"
            self._pending_remote_file_picker_message = (
                "Episode lookup returned 404; choose from the full series list."
            )
        else:
            self.remote_state.status = f"fetched {added_count} track(s)"

    def fetch_remote_tracks(self) -> tuple[int, int]:
        state = self.remote_state
        if state.source is None or state.external_id is None:
            raise ValueError("Choose --anilist or --tvdb before fetching subtitles")
        if state.episode_number is None:
            raise ValueError("Episode could not be parsed; pass --episode or press F6")

        client = self._new_subtitle_client()
        response = self._remote_file_list(client)
        insertion_idx = self._remote_insertion_index()
        first_idx = insertion_idx
        added = 0
        for file in response.files:
            if not is_supported_remote_subtitle(file):
                continue
            subtitle_id = str(file.get("subtitle_id") or "")
            if subtitle_id and any(
                existing.subtitle_id == subtitle_id for existing in self.tracks
            ):
                continue
            track = self._track_from_remote_file(client, file)
            self.tracks.insert(insertion_idx, track)
            self.cue_indices.insert(insertion_idx, 0)
            insertion_idx += 1
            added += 1

        added_tracks = self.tracks[first_idx:insertion_idx]
        self.sort_tracks_by_language()
        if added_tracks:
            first_idx = min(self.tracks.index(track) for track in added_tracks)
        if self.track_index >= len(self.tracks):
            self.track_index = max(0, len(self.tracks) - 1)
        return added, first_idx

    def _open_remote_file_picker(self, message: str) -> None:
        try:
            response = self.fetch_remote_series_files()
        except Exception as exc:  # pragma: no cover - transport details vary.
            self.remote_state.status = f"series fetch failed: {exc}"
            self.notify(str(exc), severity="error")
            self.refresh_view()
            return
        files = tuple(
            file for file in response.files if is_supported_remote_subtitle(file)
        )
        if not files:
            self.remote_state.status = "series has no subtitle files"
            self.notify("Series lookup returned no subtitle files", severity="error")
            self.refresh_view()
            return
        self.remote_state.status = f"series has {len(files)} subtitle file(s)"
        self.refresh_view()
        self.push_screen(
            RemoteFilePickModal(files, message=message),
            self.apply_manual_remote_pick,
        )

    def apply_manual_remote_pick(
        self, request: ManualSubtitlePickRequest | None
    ) -> None:
        if request is None:
            return
        client = self._new_subtitle_client()
        try:
            track = self._track_from_remote_file(client, request.file)
        except Exception as exc:  # pragma: no cover
            self.remote_state.status = f"download failed: {exc}"
            self.notify(str(exc), severity="error")
            self.refresh_view()
            return
        insertion_idx = self._remote_insertion_index()
        self.tracks.insert(insertion_idx, track)
        self.cue_indices.insert(insertion_idx, 0)
        self.track_index = insertion_idx
        self.sort_tracks_by_language()
        self.track_index = self.tracks.index(track)
        self.select_cue_near_time(self.window_start_s + self.window_s / 2)
        self.remote_state.status = "picked 1 track"
        self.notify(f"Loaded {track.path.name}")
        self.refresh_view()

    def fetch_remote_series_files(self) -> KitsunekkoFileListResponse:
        return fetch_series_files(
            self._new_subtitle_client(),
            self._subtitle_lookup(require_episode=False),
        )

    def _remote_insertion_index(self) -> int:
        if self.promotion_target is None:
            return len(self.tracks)
        destination = sidecar_path(self.promotion_target)
        for index, track in enumerate(self.tracks):
            if track.path == destination:
                return index
        return len(self.tracks)

    def _should_offer_manual_pick(self, exc: Exception) -> bool:
        if re.search(r"\b404\b", str(exc)) is None:
            return False
        try:
            response = self.fetch_remote_series_files()
        except Exception:
            return False
        return any(is_supported_remote_subtitle(file) for file in response.files)

    def _remote_file_list(
        self,
        client: HttpKitsunekkoSubtitlesClient,
    ) -> KitsunekkoFileListResponse:
        return fetch_episode_files(client, self._subtitle_lookup(require_episode=True))

    def _subtitle_lookup(self, *, require_episode: bool) -> SubtitleLookup:
        state = self.remote_state
        if state.source is None or state.external_id is None:
            raise ValueError("Choose --anilist or --tvdb before fetching subtitles")
        if require_episode and state.episode_number is None:
            raise ValueError("Episode could not be parsed; pass --episode or press F6")
        return SubtitleLookup(
            source=state.source,
            external_id=state.external_id,
            episode_number=state.episode_number,
            media_kind=state.media_kind,
        )

    def _track_from_remote_file(
        self,
        client: HttpKitsunekkoSubtitlesClient,
        file: dict[str, Any],
    ) -> SubtitleTrack:
        return materialize_remote_track(
            client,
            file,
            download_dir=self.download_dir,
            language_id_config=self.language_id_config,
        )
