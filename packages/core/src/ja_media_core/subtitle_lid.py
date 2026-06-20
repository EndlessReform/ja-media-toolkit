from __future__ import annotations

import html
import os
import re
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, model_validator

from ja_media_core.transcripts import SubtitleCue, read_srt


HTML_TAG_RE = re.compile(r"<[^>]+>")
WHITESPACE_RE = re.compile(r"\s+")

LineLanguageDetector = Callable[[str], str]


class SubtitleLanguage(StrEnum):
    """Coarse language buckets useful for ranking subtitle candidates."""

    JAPANESE = "japanese"
    UNKNOWN = "unknown"
    BILINGUAL = "bilingual"
    NON_JAPANESE = "non_japanese"
    INSUFFICIENT_TEXT = "insufficient_text"

    @property
    def rank(self) -> int:
        """Return a stable best-first candidate rank."""

        return {
            SubtitleLanguage.JAPANESE: 0,
            SubtitleLanguage.UNKNOWN: 1,
            SubtitleLanguage.BILINGUAL: 2,
            SubtitleLanguage.NON_JAPANESE: 3,
            SubtitleLanguage.INSUFFICIENT_TEXT: 4,
        }[self]


class SubtitleLanguageIdConfig(BaseModel):
    """Thresholds for inexpensive script analysis and sampled model LID.

    Defaults are deliberately more permissive than a corpus-cleaning filter:
    candidate ranking should retain uncertain Japanese subtitles and merely
    place bilingual or foreign tracks later.
    """

    minimum_lines: int = Field(default=5, ge=0)
    minimum_characters: int = Field(default=50, ge=0)
    min_line_characters: int = Field(default=5, ge=1)
    sample_lines: int = Field(default=50, ge=1)
    top_languages: int = Field(default=4, ge=1)
    low_memory: bool = False

    obvious_japanese_script_ratio: float = Field(default=0.70, ge=0.0, le=1.0)
    obvious_kana_ratio: float = Field(default=0.08, ge=0.0, le=1.0)
    obvious_max_foreign_script_ratio: float = Field(
        default=0.15, ge=0.0, le=1.0
    )
    strong_foreign_script_ratio: float = Field(default=0.50, ge=0.0, le=1.0)

    japanese_lid_ratio: float = Field(default=0.60, ge=0.0, le=1.0)
    bilingual_lid_ratio: float = Field(default=0.15, ge=0.0, le=1.0)
    bilingual_japanese_script_ratio: float = Field(
        default=0.10, ge=0.0, le=1.0
    )
    bilingual_foreign_script_ratio: float = Field(
        default=0.10, ge=0.0, le=1.0
    )

    @model_validator(mode="after")
    def validate_threshold_order(self) -> SubtitleLanguageIdConfig:
        if self.bilingual_lid_ratio > self.japanese_lid_ratio:
            raise ValueError(
                "bilingual_lid_ratio cannot exceed japanese_lid_ratio"
            )
        return self


@dataclass(frozen=True)
class SubtitleScriptMetrics:
    """Unicode script evidence measured across visible subtitle text."""

    substantive_lines: int
    visible_characters: int
    long_lines: int
    long_lines_without_kana: int
    kana_characters: int
    han_characters: int
    japanese_script_characters: int
    latin_characters: int
    cyrillic_characters: int
    hangul_characters: int
    kana_ratio: float
    long_line_no_kana_ratio: float
    japanese_script_ratio: float
    latin_ratio: float
    cyrillic_ratio: float
    hangul_ratio: float
    foreign_script_ratio: float


@dataclass(frozen=True)
class SampledLanguageMetrics:
    """Line-level language labels returned by the optional model stage."""

    sampled_lines: int
    japanese_ratio: float
    top_languages: tuple[tuple[str, float], ...]


@dataclass(frozen=True)
class SubtitleLanguageAnalysis:
    """Language evidence and the coarse bucket derived from it."""

    language: SubtitleLanguage
    reason: str
    script: SubtitleScriptMetrics
    sampled: SampledLanguageMetrics | None = None

    @property
    def sort_key(self) -> tuple[int, float, float]:
        """Return a best-first key for sorting subtitle candidates."""

        sampled_japanese = (
            self.sampled.japanese_ratio if self.sampled is not None else 0.0
        )
        return (
            self.language.rank,
            -sampled_japanese,
            -self.script.japanese_script_ratio,
        )


def analyze_srt_language(
    path: str | Path,
    *,
    config: SubtitleLanguageIdConfig | None = None,
    detector: LineLanguageDetector | None = None,
) -> SubtitleLanguageAnalysis:
    """Read an SRT and classify its language for candidate ranking."""

    return analyze_subtitle_language(
        read_srt(path),
        config=config,
        detector=detector,
    )


def analyze_subtitle_language(
    cues: Iterable[SubtitleCue],
    *,
    config: SubtitleLanguageIdConfig | None = None,
    detector: LineLanguageDetector | None = None,
) -> SubtitleLanguageAnalysis:
    """Classify subtitle cues using scripts first and sampled LID when needed.

    The cheap Unicode pass handles obviously Japanese and obviously foreign
    tracks. Ambiguous tracks pay for FastText, sampled evenly across the file so
    openings, endings, and isolated translation notes do not dominate.
    """

    options = config or SubtitleLanguageIdConfig()
    lines = subtitle_text_lines(cues)
    script = calculate_script_metrics(
        lines,
        long_line_min_characters=options.min_line_characters,
    )

    if (
        script.substantive_lines < options.minimum_lines
        or script.visible_characters < options.minimum_characters
    ):
        return SubtitleLanguageAnalysis(
            language=SubtitleLanguage.INSUFFICIENT_TEXT,
            reason=(
                f"{script.substantive_lines} substantive lines and "
                f"{script.visible_characters} visible characters"
            ),
            script=script,
        )

    if (
        script.japanese_script_ratio
        >= options.obvious_japanese_script_ratio
        and script.kana_ratio >= options.obvious_kana_ratio
        and script.foreign_script_ratio
        <= options.obvious_max_foreign_script_ratio
    ):
        return SubtitleLanguageAnalysis(
            language=SubtitleLanguage.JAPANESE,
            reason=(
                "strong Japanese script profile "
                f"(Japanese={script.japanese_script_ratio:.1%}, "
                f"kana={script.kana_ratio:.1%}, "
                f"foreign={script.foreign_script_ratio:.1%})"
            ),
            script=script,
        )

    if (
        script.foreign_script_ratio >= options.strong_foreign_script_ratio
        and script.japanese_script_ratio
        < options.bilingual_japanese_script_ratio
    ):
        return SubtitleLanguageAnalysis(
            language=SubtitleLanguage.NON_JAPANESE,
            reason=(
                "strong foreign script profile "
                f"(foreign={script.foreign_script_ratio:.1%}, "
                f"Japanese={script.japanese_script_ratio:.1%})"
            ),
            script=script,
        )

    sampled = sample_line_languages(
        lines,
        detector=detector or fasttext_line_detector(options.low_memory),
        sample_lines=options.sample_lines,
        min_line_characters=options.min_line_characters,
        top_languages=options.top_languages,
    )
    if sampled.sampled_lines == 0:
        return SubtitleLanguageAnalysis(
            language=SubtitleLanguage.UNKNOWN,
            reason="no lines were eligible for sampled language identification",
            script=script,
            sampled=sampled,
        )

    if sampled.japanese_ratio >= options.japanese_lid_ratio:
        language = SubtitleLanguage.JAPANESE
    elif (
        sampled.japanese_ratio >= options.bilingual_lid_ratio
        and script.japanese_script_ratio
        >= options.bilingual_japanese_script_ratio
        and script.foreign_script_ratio
        >= options.bilingual_foreign_script_ratio
    ):
        language = SubtitleLanguage.BILINGUAL
    else:
        language = SubtitleLanguage.NON_JAPANESE

    top = ", ".join(
        f"{name}={ratio:.1%}" for name, ratio in sampled.top_languages
    )
    return SubtitleLanguageAnalysis(
        language=language,
        reason=(
            f"sampled LID Japanese={sampled.japanese_ratio:.1%} across "
            f"{sampled.sampled_lines} lines"
            + (f" ({top})" if top else "")
        ),
        script=script,
        sampled=sampled,
    )


def subtitle_text_lines(cues: Iterable[SubtitleCue]) -> list[str]:
    """Normalize cue text into non-empty lines suitable for script/LID work."""

    lines: list[str] = []
    for cue in cues:
        text = HTML_TAG_RE.sub("", html.unescape(cue.text))
        text = unicodedata.normalize("NFKC", text)
        lines.extend(
            normalized
            for raw_line in text.splitlines()
            if (normalized := WHITESPACE_RE.sub(" ", raw_line).strip())
        )
    return lines


def visible_characters(text: str) -> list[str]:
    """Return letters and numbers, excluding punctuation and whitespace."""

    return [
        character
        for character in text
        if unicodedata.category(character)[0] in {"L", "N"}
    ]


def calculate_script_metrics(
    lines: Iterable[str],
    *,
    long_line_min_characters: int = 5,
) -> SubtitleScriptMetrics:
    """Measure Japanese and common foreign scripts in normalized text lines."""

    substantive_lines = 0
    long_lines = 0
    long_lines_without_kana = 0
    visible: list[str] = []
    for line in lines:
        line_characters = visible_characters(line)
        if not line_characters:
            continue
        substantive_lines += 1
        visible.extend(line_characters)
        if len(line_characters) >= long_line_min_characters:
            long_lines += 1
            if not any(is_kana(character) for character in line_characters):
                long_lines_without_kana += 1

    total = len(visible)
    kana = sum(is_kana(character) for character in visible)
    han = sum(is_han(character) for character in visible)
    latin = sum(is_latin(character) for character in visible)
    cyrillic = sum(is_cyrillic(character) for character in visible)
    hangul = sum(is_hangul(character) for character in visible)
    japanese = kana + han

    def ratio(count: int) -> float:
        return count / total if total else 0.0

    return SubtitleScriptMetrics(
        substantive_lines=substantive_lines,
        visible_characters=total,
        long_lines=long_lines,
        long_lines_without_kana=long_lines_without_kana,
        kana_characters=kana,
        han_characters=han,
        japanese_script_characters=japanese,
        latin_characters=latin,
        cyrillic_characters=cyrillic,
        hangul_characters=hangul,
        kana_ratio=ratio(kana),
        long_line_no_kana_ratio=(
            long_lines_without_kana / long_lines if long_lines else 0.0
        ),
        japanese_script_ratio=ratio(japanese),
        latin_ratio=ratio(latin),
        cyrillic_ratio=ratio(cyrillic),
        hangul_ratio=ratio(hangul),
        foreign_script_ratio=ratio(latin + cyrillic + hangul),
    )


def sample_line_languages(
    lines: Iterable[str],
    *,
    detector: LineLanguageDetector,
    sample_lines: int,
    min_line_characters: int,
    top_languages: int,
) -> SampledLanguageMetrics:
    """Run a line detector over an evenly distributed bounded sample."""

    eligible = [
        line
        for line in lines
        if len(visible_characters(line)) >= min_line_characters
    ]
    sampled = evenly_spaced_sample(eligible, sample_lines)
    if not sampled:
        return SampledLanguageMetrics(0, 0.0, ())

    counts = Counter(detector(line) for line in sampled)
    total = len(sampled)
    top = tuple(
        (language, count / total)
        for language, count in counts.most_common(top_languages)
    )
    return SampledLanguageMetrics(
        sampled_lines=total,
        japanese_ratio=counts["ja"] / total,
        top_languages=top,
    )


def evenly_spaced_sample(lines: list[str], limit: int) -> list[str]:
    """Select up to ``limit`` lines spanning the complete subtitle."""

    if len(lines) <= limit:
        return lines
    if limit == 1:
        return [lines[len(lines) // 2]]
    last = len(lines) - 1
    return [lines[round(index * last / (limit - 1))] for index in range(limit)]


def fasttext_line_detector(low_memory: bool = False) -> LineLanguageDetector:
    """Build the default FastText detector without importing it at module load."""

    cache_home = Path(
        os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")
    )
    os.environ.setdefault(
        "FTLANG_CACHE",
        str(cache_home / "ja-media-toolkit" / "fasttext"),
    )
    from ftlangdetect import detect

    def detect_language(line: str) -> str:
        result: Mapping[str, Any] = detect(line, low_memory=low_memory)
        language = result.get("lang")
        if not isinstance(language, str):
            raise RuntimeError(f"FastText returned no language label: {result!r}")
        return language

    return detect_language


def is_kana(character: str) -> bool:
    codepoint = ord(character)
    return (
        0x3040 <= codepoint <= 0x309F
        or 0x30A0 <= codepoint <= 0x30FF
        or 0x31F0 <= codepoint <= 0x31FF
    )


def is_han(character: str) -> bool:
    codepoint = ord(character)
    return (
        0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0xF900 <= codepoint <= 0xFAFF
        or 0x20000 <= codepoint <= 0x2FA1F
    )


def is_latin(character: str) -> bool:
    return "LATIN" in unicodedata.name(character, "")


def is_cyrillic(character: str) -> bool:
    return "CYRILLIC" in unicodedata.name(character, "")


def is_hangul(character: str) -> bool:
    return "HANGUL" in unicodedata.name(character, "")
