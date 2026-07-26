"""Content-addressed subtitle acquisition for a Phase 0 dataset."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from typing import Any

from ja_media_core.http import ServiceHttpError
from ja_media_core.kitsunekko import HttpKitsunekkoSubtitlesClient

from subtitle_alignment.access import DevReadAccess
from subtitle_alignment.silver import SilverSelection
from subtitle_alignment.values import positive_episode_number


def cache_embedded(
    root: Path, selection: SilverSelection, access: DevReadAccess
) -> list[dict[str, object]]:
    """Cache every embedded subtitle referenced by the selected Silver rows."""

    results = []
    for index, locator in enumerate(selection.subtitles, start=1):
        body = access.bronze.read_text(locator.object_key).encode("utf-8")
        digest, relative = _store_object(root, "embedded", body)
        results.append(
            {
                **asdict(locator),
                "content_hash": digest,
                "relative_path": relative,
                "byte_count": len(body),
            }
        )
        if index % 100 == 0:
            print(f"embedded={index}/{len(selection.subtitles)}")
    return results


def cache_kitsunekko(
    root: Path,
    client: HttpKitsunekkoSubtitlesClient,
    candidates: list[dict[str, Any]],
    *,
    workers: int,
) -> list[dict[str, object]]:
    """Cache candidate bytes while retaining advertised-but-missing rows."""

    identifiers = sorted({str(item["subtitle_id"]) for item in candidates})
    with ThreadPoolExecutor(max_workers=workers) as pool:
        fetched = dict(pool.map(lambda item: _fetch(client, item), identifiers))
    cached = []
    for item in candidates:
        subtitle_id = str(item["subtitle_id"])
        body, fetch_status = fetched[subtitle_id]
        digest = relative = None
        byte_count = None
        if body is not None:
            digest, relative = _store_object(root, "kitsunekko", body)
            byte_count = len(body)
        cached.append(
            {
                "anilist_id": int(item["anilist_id"]),
                "canonical_episode": int(item["canonical_episode"]),
                "subtitle_id": subtitle_id,
                "repo_path": str(item.get("repo_path") or ""),
                "filename": str(item.get("filename") or ""),
                "extension": str(item.get("extension") or ""),
                "episode_local": positive_episode_number(item.get("episode_local")),
                "content_hash": digest,
                "relative_path": relative,
                "byte_count": byte_count,
                "fetch_status": fetch_status,
                "metadata_json": json.dumps(item, ensure_ascii=False, sort_keys=True),
            }
        )
    available = sum(item["fetch_status"] == "ok" for item in cached)
    print(f"kitsunekko_downloaded={available}/{len(identifiers)}")
    return cached


def _fetch(
    client: HttpKitsunekkoSubtitlesClient, subtitle_id: str
) -> tuple[str, tuple[bytes | None, str]]:
    try:
        return subtitle_id, (client.file_content(subtitle_id), "ok")
    except ServiceHttpError as error:
        print(
            f"kitsunekko_unavailable={subtitle_id} status={error.status_code}"
        )
        return subtitle_id, (None, f"http_{error.status_code}")


def _store_object(root: Path, kind: str, body: bytes) -> tuple[str, str]:
    digest = hashlib.sha256(body).hexdigest()
    relative = Path("objects") / kind / digest
    destination = root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        destination.write_bytes(body)
    return digest, relative.as_posix()
