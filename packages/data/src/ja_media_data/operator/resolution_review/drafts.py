"""Validation and process-local state for one complete resolution draft."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from ja_media_data.operator.resolution_review.models import SeriesResolutionDraft


class SourceTokenLike(Protocol):
    """The exact automatic/control inputs against which a draft was made."""

    resolution_materialization_id: str
    snapshot_id: int
    binding_revision: int
    disposition_revision: int


IssueLister = Callable[[int, int, int], list[dict]]
MetadataGetter = Callable[[int], dict]
LocatorChecker = Callable[[int, str, str], None]
ComparisonStatus = Callable[[str, int, str], str]
DraftApplier = Callable[[SeriesResolutionDraft, SourceTokenLike], dict[str, Any]]


class DraftWorkspace:
    """Own one replaceable draft without adding a pending persistence model."""

    def __init__(
        self,
        *,
        source_token: SourceTokenLike,
        list_issues: IssueLister,
        get_metadata: MetadataGetter,
        check_locator: LocatorChecker,
        comparison_status: ComparisonStatus,
        apply_draft: DraftApplier | None,
    ) -> None:
        self.source_token = source_token
        self._list_issues = list_issues
        self._get_metadata = get_metadata
        self._check_locator = check_locator
        self._comparison_status = comparison_status
        self._apply_draft = apply_draft
        self._draft: SeriesResolutionDraft | None = None

    def save(self, current_anilist_id: int, draft: SeriesResolutionDraft) -> dict:
        """Replace the draft after validating every capture and locator."""

        if draft.current_anilist_id != current_anilist_id:
            raise ValueError(
                "draft current_anilist_id does not match the review series"
            )
        issues = {
            item["capture_id"]: item
            for item in self._list_issues(current_anilist_id, 0, 50)
        }
        flattened = flatten_draft(draft)
        capture_ids = [item["capture_id"] for item in flattened]
        if len(capture_ids) != len(set(capture_ids)):
            raise ValueError("a capture may appear only once in a draft")
        missing = sorted(set(capture_ids) - issues.keys())
        if missing:
            raise ValueError(f"captures are not open issues in this series: {missing}")

        locators: set[tuple[int, str]] = set()
        destination_ids: set[int] = set()
        for item in flattened:
            destination = item["destination_anilist_id"]
            episode = item["destination_episode"]
            if destination is None:
                continue
            if item["decision"] == "move_to_another_series":
                if destination == current_anilist_id:
                    raise ValueError(
                        "a move destination must differ from current_anilist_id"
                    )
                destination_ids.add(destination)
            locator = destination, episode
            if locator in locators:
                raise ValueError(
                    "each canonical episode locator accepts exactly one capture; "
                    f"draft assigns multiple captures to anilist:{destination}:{episode}. "
                    "Do not choose between releases from filename quality tags alone; "
                    "omit unresolved captures from the draft and explain them in summary"
                )
            locators.add(locator)
            self._check_locator(destination, episode, item["capture_id"])

        for destination in sorted(destination_ids):
            try:
                self._get_metadata(destination)
            except Exception as error:
                raise ValueError(
                    f"destination AniList ID was not found: {destination}"
                ) from error
        self._draft = draft
        return self.preview()

    def preview(self) -> dict:
        """Return the entire draft in the exact human approval shape."""

        if self._draft is None:
            raise RuntimeError("no resolution draft has been saved")
        flattened = flatten_draft(self._draft)
        issues = {
            item["capture_id"]: item
            for item in self._list_issues(self._draft.current_anilist_id, 0, 50)
        }
        anilist_ids = {self._draft.current_anilist_id}
        anilist_ids.update(
            item["destination_anilist_id"]
            for item in flattened
            if item["destination_anilist_id"] is not None
        )
        identities = {
            anilist_id: _anilist_identity(self._get_metadata(anilist_id))
            for anilist_id in sorted(anilist_ids)
        }
        rows = []
        for item in flattened:
            issue = issues[item["capture_id"]]
            destination = item["destination_anilist_id"]
            rows.append(
                {
                    "capture_id": item["capture_id"],
                    "filename": issue["source_hint"],
                    "current_anilist_id": self._draft.current_anilist_id,
                    "current_anilist": identities[self._draft.current_anilist_id],
                    "proposed_anilist": identities.get(destination),
                    "subtitle_comparison": (
                        self._comparison_status(
                            item["capture_id"], destination, item["destination_episode"]
                        )
                        if item["decision"] == "move_to_another_series"
                        else "not_required"
                    ),
                    **{
                        key: value for key, value in item.items() if key != "capture_id"
                    },
                }
            )
        return {"summary": self._draft.summary, "rows": rows}

    def apply(self) -> dict:
        """Apply the draft, failing closed until a durable writer is injected."""

        if self._draft is None:
            raise RuntimeError("no resolution draft has been saved")
        if self._apply_draft is None:
            raise RuntimeError("resolution writeback is not installed")
        return self._apply_draft(self._draft, self.source_token)


def flatten_draft(draft: SeriesResolutionDraft) -> list[dict]:
    """Flatten grouped decisions into one unambiguous row per capture."""

    rows: list[dict] = []
    for decision in draft.decisions:
        if decision.decision == "leave_out_of_episode_index":
            rows.extend(
                {
                    "capture_id": capture_id,
                    "decision": decision.decision,
                    "destination_anilist_id": None,
                    "destination_episode": None,
                    "outcome": "left_out",
                    "rationale": decision.rationale,
                }
                for capture_id in decision.capture_ids
            )
            continue
        destination = (
            draft.current_anilist_id
            if decision.decision == "keep_in_current_series"
            else decision.destination_anilist_id
        )
        rows.extend(
            {
                "capture_id": item.capture_id,
                "decision": decision.decision,
                "destination_anilist_id": destination,
                "destination_episode": str(item.episode),
                "outcome": "canonical",
                "rationale": decision.rationale,
            }
            for item in decision.files
        )
    return rows


def _anilist_identity(metadata: dict) -> dict:
    """Build the compact linked identity shared by proposal renderers."""

    anilist_id = int(metadata["anilist_id"])
    titles = []
    for key in ("title_english", "title_romaji", "title_native"):
        value = metadata.get(key)
        if value and value not in titles:
            titles.append(str(value))
    title = titles[0] if titles else f"AniList {anilist_id}"
    facts = [f"AniList {anilist_id}", " / ".join(titles)]
    season = " ".join(
        _display_value(value)
        for value in (metadata.get("season"), metadata.get("seasonYear"))
        if value
    )
    if season:
        facts.append(season)
    if metadata.get("format"):
        facts.append(_display_value(metadata["format"]))
    if metadata.get("episodes"):
        facts.append(f"{_display_value(metadata['episodes'])} episodes")
    return {
        "anilist_id": anilist_id,
        "title": title,
        "tooltip": " · ".join(item for item in facts if item),
        "url": f"https://anilist.co/anime/{anilist_id}",
    }


def _display_value(value: object) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)
