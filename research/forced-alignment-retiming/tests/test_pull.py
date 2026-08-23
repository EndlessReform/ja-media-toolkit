"""Candidate downloads remain bounded to the supplied episode inventory."""

from pathlib import Path

from ja_media_core.kitsunekko import KitsunekkoFileListResponse

from forced_alignment_retiming.cases import CanonicalCase
from forced_alignment_retiming.pull import pull_candidates


class StubClient:
    def __init__(self) -> None:
        self.fetched: list[str] = []

    def anilist_episode_files(
        self, anilist_id: int, episode_number: int
    ) -> KitsunekkoFileListResponse:
        assert (anilist_id, episode_number) == (7647, 9)
        return KitsunekkoFileListResponse(
            count=2,
            episode_number=9,
            files=(
                {"subtitle_id": "candidate-b", "extension": "ass", "repo_path": "b"},
                {"subtitle_id": "candidate-a", "extension": "srt", "repo_path": "a"},
            ),
        )

    def file_content(self, file_ref: str) -> bytes:
        self.fetched.append(file_ref)
        return {"candidate-a": b"one", "candidate-b": b"two"}[file_ref]


def test_pulls_only_episode_inventory(tmp_path: Path) -> None:
    client = StubClient()
    selected = CanonicalCase("happy", "anilist", "7647", "9")

    manifest = pull_candidates(
        selected,
        {"materialization_id": "materialization-1", "snapshot_id": 7},
        tmp_path,
        client=client,  # type: ignore[arg-type]
    )

    assert client.fetched == ["candidate-a", "candidate-b"]
    assert manifest.is_file()
    assert len(list((tmp_path / "happy" / "candidates").iterdir())) == 2
    assert '"mapped_episode": 9' in manifest.read_text()
