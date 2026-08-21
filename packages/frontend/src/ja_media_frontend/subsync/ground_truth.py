"""Embedded subtitle discovery for subsync alignment hints."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ja_media_core.anime_audio import AnimeAudioClient, HttpAnimeAudioClient
from ja_media_core.anime_audio_models import AnimeAudioSubtitle
from ja_media_core.transcripts import parse_srt
from ja_media_frontend.subsync.service import SubtitleTrack


LANGUAGE_PRIORITY = (
    frozenset(("ko", "kor", "korean")),
    frozenset(("en", "eng", "english")),
    frozenset(("de", "deu", "ger", "german")),
)


@dataclass(frozen=True)
class GroundTruthSubtitleResult:
    """Best-effort embedded subtitle track selected for timing comparison."""

    track: SubtitleTrack | None
    status: str


def discover_ground_truth_subtitle(
    *,
    anilist_id: int | None,
    episode_number: int | None,
    download_dir: Path,
    client: AnimeAudioClient | None = None,
) -> GroundTruthSubtitleResult:
    """Fetch the preferred embedded subtitle for the active episode, if any.

    This is intentionally soft-fail: the TUI can still align community
    candidates against local audio when indexed embedded subtitles are missing
    or the LAN service is unavailable.
    """

    if anilist_id is None or episode_number is None:
        return GroundTruthSubtitleResult(None, "")

    episode_key = str(episode_number)
    try:
        audio_client = client or HttpAnimeAudioClient()
        subtitles = audio_client.subtitles(anilist_id, episode_key)
    except Exception as exc:
        return GroundTruthSubtitleResult(None, f"truth unavailable: {exc}")
    if not subtitles:
        return GroundTruthSubtitleResult(None, "no embedded subs")

    subtitle = _preferred_subtitle(subtitles)
    try:
        content = audio_client.subtitle_content(
            anilist_id,
            episode_key,
            subtitle.subtitle_id,
        )
        path = _write_ground_truth_file(download_dir, subtitle, content)
        cues = parse_srt(content.decode("utf-8-sig", errors="replace"), source_path=path)
    except Exception as exc:
        return GroundTruthSubtitleResult(None, f"truth unavailable: {exc}")
    if not cues:
        return GroundTruthSubtitleResult(None, "embedded sub has no cues")

    label = _subtitle_label(subtitle)
    return GroundTruthSubtitleResult(
        SubtitleTrack(
            path=path,
            cues=cues,
            repo_path=f"embedded/{label}",
            subtitle_id=subtitle.subtitle_id,
        ),
        f"truth {label}",
    )


def _preferred_subtitle(
    subtitles: tuple[AnimeAudioSubtitle, ...],
) -> AnimeAudioSubtitle:
    return min(subtitles, key=_subtitle_sort_key)


def _subtitle_sort_key(subtitle: AnimeAudioSubtitle) -> tuple[int, int, int]:
    language = _normalized_language(subtitle.language)
    for index, aliases in enumerate(LANGUAGE_PRIORITY):
        if language in aliases:
            return (index, int(not subtitle.default), subtitle.source_stream_index)
    return (len(LANGUAGE_PRIORITY), int(not subtitle.default), subtitle.source_stream_index)


def _normalized_language(language: str | None) -> str:
    return (language or "").strip().lower().replace("_", "-").split("-", 1)[0]


def _write_ground_truth_file(
    download_dir: Path,
    subtitle: AnimeAudioSubtitle,
    content: bytes,
) -> Path:
    download_dir.mkdir(parents=True, exist_ok=True)
    filename = Path(subtitle.filename).name or f"{subtitle.subtitle_id}.srt"
    path = download_dir / f"embedded-{subtitle.subtitle_id}-{filename}"
    path.write_bytes(content)
    return path


def _subtitle_label(subtitle: AnimeAudioSubtitle) -> str:
    language = subtitle.language or "unknown"
    title = f" {subtitle.title}" if subtitle.title else ""
    return f"{language}{title}".strip()
