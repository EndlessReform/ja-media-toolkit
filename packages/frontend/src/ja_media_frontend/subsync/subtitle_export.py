"""Atomic SRT export independent of a media sidecar destination."""

import os
import uuid
from pathlib import Path

from ja_media_core.subsync import SubtitleCandidate
from ja_media_core.transcripts import format_srt


def save_subtitle(candidate: SubtitleCandidate, destination: Path, *, overwrite: bool = False) -> Path:
    """Save the selected candidate, including in-memory timing edits, as SRT."""
    if destination.suffix.lower() != ".srt":
        raise ValueError("Choose a filename ending in .srt")
    if destination.exists() and not overwrite:
        raise FileExistsError(destination)
    if candidate.path.suffix.lower() == ".srt" and not getattr(candidate, "modified", False):
        content = candidate.path.read_bytes()
    else:
        content = format_srt(candidate.cues).encode("utf-8")
    _atomic_write_bytes(destination, content)
    return destination


def _atomic_write_bytes(destination: Path, content: bytes) -> None:
    """Write bytes beside the destination and atomically replace it.

    This deliberately avoids ``shutil.copy2``: sidecars need content, not source
    mode and timestamp metadata, and some NFS mounts reject metadata updates
    after otherwise successful writes.
    """

    tmp_path: Path | None = None
    try:
        for _ in range(100):
            candidate = destination.with_name(
                f".{destination.name}.{uuid.uuid4().hex}.tmp"
            )
            try:
                fd = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
            except FileExistsError:
                continue
            tmp_path = candidate
            break
        else:  # pragma: no cover - UUID collisions are not realistically reachable.
            raise FileExistsError(f"Could not allocate temp file for {destination}")

        with os.fdopen(fd, "wb") as output_file:
            output_file.write(content)
            output_file.flush()
            os.fsync(output_file.fileno())

        os.replace(tmp_path, destination)
        tmp_path = None
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
