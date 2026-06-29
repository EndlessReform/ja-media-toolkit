from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any


RUN_SCHEMA_NAME = "ja-media.srt-clean.run"
RUN_SCHEMA_VERSION = "1.0.0"
WINDOW_SCHEMA_NAME = "ja-media.srt-clean.window"
WINDOW_SCHEMA_VERSION = "1.1.0"


@dataclass(frozen=True)
class SrtCleanRun:
    """Resolved paths for one workspace-backed SRT cleaning run."""

    anilist_id: int
    run_id: str
    workspace_root: Path
    run_dir: Path

    @property
    def manifest_path(self) -> Path:
        return self.run_dir / "manifest.jsonl"

    @property
    def run_manifest_path(self) -> Path:
        return self.run_dir / "run-manifest.json"

    @property
    def shards_summary_path(self) -> Path:
        return self.run_dir / "shards.json"

    @property
    def sources_dir(self) -> Path:
        return self.run_dir / "sources"

    @property
    def results_path(self) -> Path:
        return self.run_dir / "results.jsonl"

    @property
    def reconstruct_dir(self) -> Path:
        return self.run_dir / "reconstruct"


def default_workspace_root() -> Path:
    """Return the repo-local default run workspace."""

    return find_repo_root(Path.cwd()) / ".ja-media-runs"


def find_repo_root(start: Path) -> Path:
    """Find the nearest repository root from a command working directory."""

    current = start.expanduser().resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return current


def run_for_anilist(
    anilist_id: int,
    *,
    workspace_root: Path | None = None,
    run_id: str = "current",
) -> SrtCleanRun:
    """Resolve the workspace directory for one AniList-backed run."""

    root = (workspace_root or default_workspace_root()).expanduser().resolve()
    run_dir = root / "srt-clean" / f"anilist-{anilist_id}" / run_id
    return SrtCleanRun(
        anilist_id=anilist_id,
        run_id=run_id,
        workspace_root=root,
        run_dir=run_dir,
    )


def workspace_run_id(payload: dict[str, Any]) -> str:
    """Derive a stable short run ID from generation inputs."""

    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return f"sha256-{sha256(encoded).hexdigest()[:12]}"


def prepare_run_dir(run: SrtCleanRun, *, clobber: bool) -> None:
    """Create a run directory, optionally replacing previous contents."""

    if clobber and run.run_dir.exists():
        shutil.rmtree(run.run_dir)
    run.run_dir.mkdir(parents=True, exist_ok=True)


def write_run_manifest(
    run: SrtCleanRun,
    *,
    batch_shards: list[Path],
    model: str,
    pipeline_version: str,
    prompt_policy_sha256: str,
) -> None:
    """Write the workspace-level manifest used for autodetection."""

    payload = {
        "schema_name": RUN_SCHEMA_NAME,
        "schema_version": RUN_SCHEMA_VERSION,
        "anilist_id": run.anilist_id,
        "run_id": run.run_id,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "pipeline_version": pipeline_version,
        "prompt_policy_sha256": prompt_policy_sha256,
        "model": model,
        "paths": {
            "batch_shards": [path.relative_to(run.run_dir).as_posix() for path in batch_shards],
            "window_manifest": "manifest.jsonl",
            "shards_summary": "shards.json",
            "sources_dir": "sources",
            "results": "results.jsonl",
            "reconstruct_dir": "reconstruct",
        },
    }
    run.run_manifest_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def validate_schema_major(
    *,
    schema_name: str | None,
    schema_version: str | None,
    expected_name: str,
    expected_version: str,
    artifact: Path,
) -> None:
    """Fail loudly when a durable artifact has an incompatible schema."""

    if schema_name is None and schema_version is None:
        return
    if schema_name != expected_name:
        raise ValueError(f"{artifact} has unsupported schema {schema_name!r}")
    if schema_version is None:
        raise ValueError(f"{artifact} is missing schema_version")
    if schema_version.split(".", 1)[0] != expected_version.split(".", 1)[0]:
        raise ValueError(
            f"{artifact} uses schema {schema_version}; expected {expected_version}"
        )
