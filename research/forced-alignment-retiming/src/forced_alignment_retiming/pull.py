"""Download Kitsunekko candidates for one canonical episode."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from ja_media_core.http import ServiceHttpError
from ja_media_core.kitsunekko import HttpKitsunekkoSubtitlesClient

from forced_alignment_retiming.cases import CanonicalCase


_SAFE_EXTENSION = re.compile(r"^[a-z0-9]{1,8}$")


def pull_candidates(
    case: CanonicalCase,
    canonical: dict[str, object],
    output_root: Path,
    *,
    client: HttpKitsunekkoSubtitlesClient | None = None,
) -> Path:
    """Fetch only the candidates mapped by Kitsunekko to the requested episode."""

    service = client or HttpKitsunekkoSubtitlesClient(timeout_s=20.0)
    inventory = service.anilist_episode_files(int(case.series_id), int(case.episode))
    case_root = output_root.resolve() / case.name
    candidate_root = case_root / "candidates"
    candidate_root.mkdir(parents=True, exist_ok=True)

    results = []
    for item in sorted(inventory.files, key=_candidate_sort_key):
        results.append(
            _download_one(
                service,
                candidate_root,
                dict(item),
                mapped_episode=inventory.episode_number,
            )
        )

    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "case": {
            "name": case.name,
            "namespace": case.namespace,
            "series_id": case.series_id,
            "episode": case.episode,
            "bootstrap_candidate_id": case.bootstrap_candidate_id,
            "prior_identity_score": case.prior_identity_score,
        },
        "canonical": canonical,
        "kitsunekko": {
            "mapped_episode": inventory.episode_number,
            "advertised_count": inventory.count,
            "downloaded_count": sum(item["status"] == "ok" for item in results),
            "failed_count": sum(item["status"] != "ok" for item in results),
        },
        "candidates": results,
    }
    destination = case_root / "candidate-pull.json"
    destination.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    return destination


def _download_one(
    client: HttpKitsunekkoSubtitlesClient,
    candidate_root: Path,
    item: dict[str, Any],
    *,
    mapped_episode: int | None,
) -> dict[str, object]:
    subtitle_id = str(item.get("subtitle_id") or "").strip()
    if not subtitle_id:
        return {
            "status": "invalid_metadata",
            "mapped_episode": mapped_episode,
            "metadata": item,
        }
    try:
        body = client.file_content(subtitle_id)
    except ServiceHttpError as error:
        return {
            "subtitle_id": subtitle_id,
            "status": f"http_{error.status_code}",
            "mapped_episode": mapped_episode,
            "metadata": item,
        }
    digest = hashlib.sha256(body).hexdigest()
    extension = str(item.get("extension") or "bin").lower().lstrip(".")
    if not _SAFE_EXTENSION.fullmatch(extension):
        extension = "bin"
    destination = candidate_root / f"{digest}.{extension}"
    if destination.exists() and destination.read_bytes() != body:
        raise RuntimeError(f"hash collision at {destination}")
    if not destination.exists():
        destination.write_bytes(body)
    return {
        "subtitle_id": subtitle_id,
        "status": "ok",
        "mapped_episode": mapped_episode,
        "sha256": digest,
        "byte_count": len(body),
        "relative_path": destination.relative_to(candidate_root.parent).as_posix(),
        "metadata": item,
    }


def _candidate_sort_key(item: dict[str, Any]) -> tuple[str, str]:
    return str(item.get("repo_path") or ""), str(item.get("subtitle_id") or "")
