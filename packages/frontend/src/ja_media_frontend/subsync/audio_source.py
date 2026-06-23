"""Resolve subsync playback audio without conflating it with promotion paths."""

from __future__ import annotations

import hashlib
import os
import tempfile
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

from ja_media_core.anime_audio import (
    AnimeAudioArtifact,
    AnimeAudioClient,
    AnimeAudioNotFoundError,
    HttpAnimeAudioClient,
)

DEFAULT_AUDIO_PROFILE = "portable-aac-v1"


@dataclass(frozen=True)
class SubsyncAudioSelection:
    """Playback input plus the optional authoritative media promotion target."""

    playback_path: Path
    promotion_target: Path | None
    status: str


def default_audio_cache_dir() -> Path:
    """Return the user cache root for fetched derived anime audio."""

    root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return root / "ja-media-toolkit" / "anime-audio"


def resolve_subsync_audio(
    source: Path | None,
    *,
    anilist_id: int | None,
    episode_number: int | None,
    profile: str = DEFAULT_AUDIO_PROFILE,
    cache_dir: Path | None = None,
    client: AnimeAudioClient | None = None,
) -> SubsyncAudioSelection:
    """Prefer an indexed artifact, retaining a local source only for fallback.

    Artifact lookup and cache validation happen before callers decode the local
    media. This keeps NFS-hosted MKVs cold when the portable artifact exists.
    """

    promotion_target = _validated_source(source)
    if anilist_id is None or episode_number is None:
        if promotion_target is None:
            raise ValueError(
                "Identity-only subsync requires both --anilist and --episode"
            )
        return SubsyncAudioSelection(
            playback_path=promotion_target,
            promotion_target=promotion_target,
            status="using supplied media audio",
        )

    episode_key = str(episode_number)
    try:
        audio_client = client or HttpAnimeAudioClient()
        artifact = audio_client.artifact(
            anilist_id,
            episode_key,
            profile=profile,
        )
        playback_path = _cached_artifact(
            audio_client,
            artifact,
            cache_dir=cache_dir or default_audio_cache_dir(),
        )
    except AnimeAudioNotFoundError as exc:
        return _fallback_or_raise(promotion_target, str(exc))
    except Exception as exc:
        return _fallback_or_raise(
            promotion_target,
            f"Derived audio service unavailable: {exc}",
        )

    return SubsyncAudioSelection(
        playback_path=playback_path,
        promotion_target=promotion_target,
        status=f"using cached derived audio ({artifact.profile})",
    )


def _validated_source(source: Path | None) -> Path | None:
    if source is None:
        return None
    resolved = source.expanduser().resolve()
    if not resolved.is_file():
        raise ValueError(f"subsync source is not a file: {resolved}")
    return resolved


def _fallback_or_raise(
    promotion_target: Path | None,
    reason: str,
) -> SubsyncAudioSelection:
    if promotion_target is None:
        raise ValueError(f"{reason}; no local media was supplied for fallback")
    return SubsyncAudioSelection(
        playback_path=promotion_target,
        promotion_target=promotion_target,
        status=f"{reason}; using supplied media audio",
    )


def _cached_artifact(
    client: AnimeAudioClient,
    artifact: AnimeAudioArtifact,
    *,
    cache_dir: Path,
) -> Path:
    filename = Path(artifact.filename).name
    if filename != artifact.filename or filename in {"", ".", ".."}:
        raise RuntimeError("Anime audio service returned an unsafe artifact filename")
    target = (
        cache_dir
        / f"anilist-{artifact.anilist_id}"
        / urllib.parse.quote(artifact.episode_key, safe="")
        / urllib.parse.quote(artifact.profile, safe="")
        / filename
    )
    if _cache_entry_matches(target, artifact):
        return target

    content = client.content(
        artifact.anilist_id,
        artifact.episode_key,
        profile=artifact.profile,
    )
    if len(content) != artifact.size_bytes:
        raise RuntimeError(
            f"Derived audio size mismatch: expected {artifact.size_bytes}, "
            f"received {len(content)}"
        )
    if artifact.sha256 and hashlib.sha256(content).hexdigest() != artifact.sha256:
        raise RuntimeError("Derived audio checksum mismatch")

    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def _cache_entry_matches(path: Path, artifact: AnimeAudioArtifact) -> bool:
    if not path.is_file() or path.stat().st_size != artifact.size_bytes:
        return False
    if artifact.sha256 is None:
        return True
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest() == artifact.sha256
