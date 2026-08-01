"""Application service behind the episode-resolution agent's small tool set."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import asdict, dataclass
from ja_media_core.anilist_search import AniListSearchClient
from ja_media_core.bronze import BronzeCaptureManifest, parse_bronze_manifest
from ja_media_core.kitsunekko import KitsunekkoSubtitlesClient

from ja_media_data.lakehouse.repository import DuckLakeRepository
from ja_media_data.lakehouse.time_travel import table_ref
from ja_media_data.products.canonical_inputs.selection import subtitle_object_key
from ja_media_data.operator.resolution_review.drafts import (
    DraftApplier,
    DraftWorkspace,
    SourceTokenLike,
)
from ja_media_data.operator.resolution_review.models import SeriesResolutionDraft
from ja_media_data.operator.resolution_review.anilist_metadata import synopsis_text
from ja_media_data.operator.resolution_review.issue_reader import read_issue_rows
from ja_media_data.operator.resolution_review.subtitle_comparison import (
    SubtitleComparator,
)
from ja_media_data.storage.bronze import BronzeStore


ANILIST_FIELDS = (
    "title_romaji",
    "title_english",
    "title_native",
    "synonyms",
    "episodes",
    "format",
    "season",
    "seasonYear",
    "startDate_year",
    "status",
    "relations",
    "description",
)


RepositoryFactory = Callable[[], AbstractContextManager[DuckLakeRepository]]


@dataclass
class ReviewContext:
    """Dependencies and series scope passed to SDK tool functions."""

    current_anilist_id: int
    toolbox: ResolutionToolbox


class ResolutionToolbox:
    """Bounded reads plus process-local draft mutation, independent of HTTP."""

    def __init__(
        self,
        *,
        repositories: RepositoryFactory,
        bronze: BronzeStore,
        anilist: AniListSearchClient,
        kitsunekko: KitsunekkoSubtitlesClient | None,
        source_token: SourceTokenLike,
        apply_draft: DraftApplier | None = None,
    ) -> None:
        self._repositories = repositories
        self._bronze = bronze
        self._anilist = anilist
        self.source_token = source_token
        self._subtitle_comparator = SubtitleComparator(
            kitsunekko=kitsunekko,
            list_capture_subtitles=self.list_subtitles,
            read_capture_subtitle=self.read_subtitle,
        )
        self._drafts = DraftWorkspace(
            source_token=source_token,
            list_issues=self.issues,
            get_metadata=self.get_anilist,
            check_locator=self._check_locator,
            comparison_status=self._subtitle_comparator.status_for,
            apply_draft=apply_draft,
        )

    def issues(self, current_anilist_id: int, offset: int, limit: int) -> list[dict]:
        """Return one bounded page from the rejected resolver product."""

        if offset < 0 or not 1 <= limit <= 50:
            raise ValueError("issue pages require offset >= 0 and limit 1..50")
        return self._issue_rows(
            "capture.series_namespace = 'anilist' AND capture.series_id = ?",
            [str(current_anilist_id)],
            limit=limit,
            offset=offset,
        )

    def issue(self, current_anilist_id: int, issue_id: str) -> dict:
        """Return one issue only when it belongs to the current review series."""

        rows = self._issue_rows(
            "issue.issue_id = ? AND capture.series_namespace = 'anilist' "
            "AND capture.series_id = ?",
            [issue_id, str(current_anilist_id)],
            limit=1,
            offset=0,
        )
        if not rows:
            raise KeyError(f"resolution issue not found in current series: {issue_id}")
        return rows[0]

    def search_anilist(self, query: str, limit: int) -> list[dict]:
        """Search every AniList format through the first-party metadata SDK."""

        query = query.strip()
        if not query or not 1 <= limit <= 10:
            raise ValueError("AniList search requires a query and limit 1..10")
        response = self._anilist.search(query, top_k=limit, all_formats=True)
        return [asdict(result) for result in response.results]

    def get_anilist(self, anilist_id: int) -> dict:
        """Return the fixed metadata fields useful for season disambiguation."""

        if anilist_id < 1:
            raise ValueError("AniList ID must be positive")
        metadata = self._anilist.anime(anilist_id, fields=ANILIST_FIELDS)
        fields = {
            key: value for key, value in metadata.fields.items() if key != "description"
        }
        return {
            "anilist_id": metadata.anilist_id,
            **fields,
            "description_text": synopsis_text(metadata.fields.get("description")),
        }

    def list_subtitles(self, current_anilist_id: int, capture_id: str) -> list[dict]:
        """List subtitle streams from the capture's pinned Bronze manifest."""

        manifest, manifest_key = self._review_manifest(current_anilist_id, capture_id)
        return [
            {
                "stream_index": stream.stream_index,
                "filename": stream.object_name,
                "codec": stream.codec,
                "declared_language": stream.declared_language,
                "object_key": subtitle_object_key(
                    manifest_key, stream.object_name, capture_stem=manifest.stem
                ),
            }
            for stream in manifest.subtitles
        ]

    def read_subtitle(
        self,
        current_anilist_id: int,
        capture_id: str,
        stream_index: int,
        start_line: int,
        line_count: int,
    ) -> str:
        """Read at most 200 numbered lines and 20 KiB from a declared stream."""

        if start_line < 1 or not 1 <= line_count <= 200:
            raise ValueError(
                "subtitle reads require start_line >= 1 and line_count 1..200"
            )
        manifest, manifest_key = self._review_manifest(current_anilist_id, capture_id)
        matching = [
            stream
            for stream in manifest.subtitles
            if stream.stream_index == stream_index
        ]
        if len(matching) != 1:
            raise KeyError(f"capture has no unique subtitle stream {stream_index}")
        object_key = subtitle_object_key(
            manifest_key, matching[0].object_name, capture_stem=manifest.stem
        )
        lines = self._bronze.read_text(object_key).splitlines()
        selected = lines[start_line - 1 : start_line - 1 + line_count]
        numbered = "\n".join(
            f"{number}: {line}"
            for number, line in enumerate(selected, start=start_line)
        )
        return _utf8_prefix(numbered, 20 * 1024)

    def save_draft(self, current_anilist_id: int, draft: SeriesResolutionDraft) -> dict:
        """Replace process-local draft state after validating the whole crosswalk."""

        return self._drafts.save(current_anilist_id, draft)

    def compare_subtitles(
        self,
        current_anilist_id: int,
        capture_id: str,
        proposed_anilist_id: int,
        proposed_episode: int,
        *,
        capture_stream_index: int | None,
        reference_subtitle_id: str | None,
        start_line: int,
        line_count: int,
    ) -> dict:
        """Compare a capture against one proposed Kitsunekko episode reference."""

        return self._subtitle_comparator.compare(
            current_anilist_id,
            capture_id,
            proposed_anilist_id,
            proposed_episode,
            capture_stream_index=capture_stream_index,
            reference_subtitle_id=reference_subtitle_id,
            start_line=start_line,
            line_count=line_count,
        )

    def preview_draft(self) -> dict:
        """Return the entire saved draft in the exact human approval shape."""

        return self._drafts.preview()

    def apply_approved_draft(self) -> dict:
        """Apply the saved draft, failing closed until a writer is injected."""

        return self._drafts.apply()

    def _issue_rows(
        self, where: str, params: list[object], *, limit: int, offset: int
    ) -> list[dict]:
        return read_issue_rows(
            self._repositories,
            self.source_token,
            where,
            params,
            limit=limit,
            offset=offset,
        )

    def _review_manifest(
        self, current_anilist_id: int, capture_id: str
    ) -> tuple[BronzeCaptureManifest, str]:
        issue = next(
            (
                item
                for item in self.issues(current_anilist_id, 0, 50)
                if item["capture_id"] == capture_id
            ),
            None,
        )
        if issue is None:
            raise KeyError(f"capture is not an open issue in this series: {capture_id}")
        if issue["manifest_bucket"] != self._bronze.bucket:
            raise ValueError("capture manifest is outside the configured Bronze bucket")
        payload = self._bronze.read_manifest(
            issue["manifest_key"], expected_etag=issue["manifest_etag"]
        )
        return parse_bronze_manifest(
            payload, capture_id=capture_id, manifest_key=issue["manifest_key"]
        ), issue["manifest_key"]

    def _check_locator(self, anilist_id: int, episode: str, capture_id: str) -> None:
        canonical = table_ref(
            "canonical_episode_inputs", snapshot_id=self.source_token.snapshot_id
        )
        with self._repositories() as repository:
            row = repository.connection.execute(
                f"""SELECT audio_capture_id FROM {canonical}
                     WHERE namespace = 'anilist' AND series_id = ? AND episode = ?
                     LIMIT 1""",
                [str(anilist_id), episode],
            ).fetchone()
            if row is not None and row[0] != capture_id:
                raise ValueError(
                    f"anilist:{anilist_id}:{episode} already has a capture"
                )
            get_override = getattr(
                repository.override_repository, "get_current_override", None
            )
            override = (
                get_override("anilist", str(anilist_id), episode)
                if get_override
                else None
            )
            if override is not None and override.audio_capture_id != capture_id:
                raise ValueError(
                    f"anilist:{anilist_id}:{episode} already has an override"
                )


def _utf8_prefix(value: str, limit: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value
    return encoded[:limit].decode("utf-8", errors="ignore") + "\n[truncated]"
