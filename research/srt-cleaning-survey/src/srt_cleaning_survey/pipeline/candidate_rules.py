from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable


RULE_READING_GLOSS = "strip_reading_gloss"
RULE_PARENTHETICAL = "strip_parenthesized_span"
RULE_BRACKETED_PREFIX = "strip_bracketed_prefix"
RULE_WIDTH = "halfwidth_alphanumeric"
RULE_MIDDLE_DOT = "normalize_middle_dot"
RULE_PARATEXT = "remove_obvious_paratext"
RULE_LANGUAGE_CAPTION = "remove_language_caption"
RULE_LEADING_SPEAKER_MARKER = "strip_leading_speaker_marker"
RULE_TERMINAL_HORIZONTAL_BAR = "strip_terminal_horizontal_bar"

FLAG_ANGLE_WRAPPED = "angle_wrapped"
FLAG_INTERNAL_SPEAKER_BOUNDARY = "internal_speaker_boundary"
FLAG_WHOLE_CUE_QUOTE = "whole_cue_quote"
FLAG_REPETITIVE_KANA = "repetitive_kana"
FLAG_VERY_SHORT_KANA = "very_short_kana"

CANDIDATE_RULES = (
    RULE_PARATEXT,
    RULE_LANGUAGE_CAPTION,
    RULE_READING_GLOSS,
    RULE_PARENTHETICAL,
    RULE_BRACKETED_PREFIX,
    RULE_LEADING_SPEAKER_MARKER,
    RULE_TERMINAL_HORIZONTAL_BAR,
    RULE_WIDTH,
    RULE_MIDDLE_DOT,
)

_BASE_CHARACTER_RE = re.compile(
    r"[一-龯々〆ヵヶぁ-んァ-ヶーA-Za-zＡ-Ｚａ-ｚ0-9０-９]"
)
_READING_RE = re.compile(r"[ぁ-んァ-ヶー・･ 　]+")
_BRACKETED_PREFIX_RE = re.compile(r"^\s*【[^】]{1,40}】\s*")
_PARATEXT_RES = (
    re.compile(r"^\s*次回(?:\s|[「『【（(]|$)"),
    re.compile(r"^\s*(?:つづく|続く)\s*[。.!！?？…⋯]*\s*$"),
)
_LANGUAGE_CAPTION_RE = re.compile(
    r"^\s*[（(][^）)\r\n]{1,40}[：:]\s*"
    r"(?:英語|中国語|韓国語|外国語)\s*[）)]"
)
_LEADING_DOUBLE_ANGLE_RE = re.compile(r"^\s*≪+\s*")
_TERMINAL_HORIZONTAL_BAR_RE = re.compile(r"[ \t　]*―+[ \t　]*$")
_INTERNAL_SPEAKER_BOUNDARY_RE = re.compile(r"\S[\s\S]*≪\s*[（(]")
_REPETITIVE_KANA_RE = re.compile(
    r"(?:([ぁ-んァ-ヶー])\1{2,}|([ぁ-んァ-ヶー]{2})\2{2,})"
)
_SHORT_KANA_RE = re.compile(r"[ぁ-んァ-ヶー]{1,2}")
_SCREENING_PUNCTUATION_RE = re.compile(r"[\s　、。,.!?！？…⋯〜～―—・･]+")
_WIDTH_TRANSLATION = str.maketrans(
    {
        **{chr(code): chr(code - 0xFEE0) for code in range(0xFF10, 0xFF1A)},
        **{chr(code): chr(code - 0xFEE0) for code in range(0xFF21, 0xFF3B)},
        **{chr(code): chr(code - 0xFEE0) for code in range(0xFF41, 0xFF5B)},
    }
)
_PAREN_PAIRS = {"(": ")", "（": "）"}


@dataclass(frozen=True)
class CandidateRuleResult:
    """Text produced by the approved deterministic pre-model cleanup rules."""

    text: str
    rules: tuple[str, ...]
    flags: tuple[str, ...] = ()

    @property
    def changed(self) -> bool:
        return bool(self.rules)

    @property
    def flagged(self) -> bool:
        return bool(self.flags)


@dataclass(frozen=True)
class _ParentheticalSpan:
    start: int
    end: int
    contents: str


def apply_candidate_rules(
    text: str,
    *,
    enabled_rules: Iterable[str] = CANDIDATE_RULES,
) -> CandidateRuleResult:
    """Apply deterministic cleanup while retaining suspicious-cue flags."""

    enabled = frozenset(enabled_rules)
    normalized = text
    fired: list[str] = []
    flags = screen_candidate_flags(text)

    if RULE_PARATEXT in enabled and any(
        pattern.match(normalized) for pattern in _PARATEXT_RES
    ):
        return CandidateRuleResult("", (RULE_PARATEXT,), flags)

    # This must run before generic parenthesis stripping. Otherwise a caption
    # such as ``（外国人：英語）日本語訳`` loses the marker that tells us the
    # remaining text is not dialogue heard in the Japanese audio.
    if RULE_LANGUAGE_CAPTION in enabled and _LANGUAGE_CAPTION_RE.match(normalized):
        return CandidateRuleResult("", (RULE_LANGUAGE_CAPTION,), flags)

    # Suspicious cues are routing candidates, not deterministic-edit candidates.
    # Preserve every marker so a later filtered model call sees the original signal.
    if flags:
        return CandidateRuleResult(normalized, (), flags)

    parenthetical_rules = {RULE_READING_GLOSS, RULE_PARENTHETICAL} & enabled
    if parenthetical_rules:
        normalized, parenthetical_fired = _strip_parenthetical_spans(
            normalized,
            enabled=parenthetical_rules,
        )
        fired.extend(parenthetical_fired)

    if RULE_BRACKETED_PREFIX in enabled:
        replaced = _BRACKETED_PREFIX_RE.sub("", normalized, count=1)
        if replaced != normalized:
            normalized = replaced
            fired.append(RULE_BRACKETED_PREFIX)

    if RULE_LEADING_SPEAKER_MARKER in enabled:
        replaced = _LEADING_DOUBLE_ANGLE_RE.sub("", normalized, count=1)
        if replaced != normalized:
            normalized = replaced
            fired.append(RULE_LEADING_SPEAKER_MARKER)

    if RULE_TERMINAL_HORIZONTAL_BAR in enabled:
        replaced = _TERMINAL_HORIZONTAL_BAR_RE.sub("", normalized)
        if replaced != normalized:
            normalized = replaced
            fired.append(RULE_TERMINAL_HORIZONTAL_BAR)

    if RULE_WIDTH in enabled:
        replaced = normalized.translate(_WIDTH_TRANSLATION)
        if replaced != normalized:
            normalized = replaced
            fired.append(RULE_WIDTH)

    if RULE_MIDDLE_DOT in enabled and "･" in normalized:
        normalized = normalized.replace("･", "・")
        fired.append(RULE_MIDDLE_DOT)

    return CandidateRuleResult(normalized, tuple(dict.fromkeys(fired)), flags)


def screen_candidate_flags(text: str) -> tuple[str, ...]:
    """Return non-destructive reasons to consider a cue for model review."""

    flags: list[str] = []
    stripped = text.strip(" \t　")
    compact = _SCREENING_PUNCTUATION_RE.sub("", stripped)
    if "〈" in stripped or "〉" in stripped:
        flags.append(FLAG_ANGLE_WRAPPED)
    if _INTERNAL_SPEAKER_BOUNDARY_RE.search(stripped):
        flags.append(FLAG_INTERNAL_SPEAKER_BOUNDARY)
    whole_cue_quote = (
        stripped.startswith("“") and stripped.endswith("”")
    ) or (stripped.startswith('"') and stripped.endswith('"'))
    if len(stripped) >= 2 and whole_cue_quote:
        flags.append(FLAG_WHOLE_CUE_QUOTE)
    if _REPETITIVE_KANA_RE.search(compact):
        flags.append(FLAG_REPETITIVE_KANA)
    if _SHORT_KANA_RE.fullmatch(compact):
        flags.append(FLAG_VERY_SHORT_KANA)
    return tuple(flags)


def _strip_parenthetical_spans(
    text: str,
    *,
    enabled: frozenset[str],
) -> tuple[str, tuple[str, ...]]:
    spans = _top_level_parenthetical_spans(text)
    removals: list[_ParentheticalSpan] = []
    fired: list[str] = []
    for span in spans:
        rule = _parenthetical_rule(text, span)
        if rule not in enabled:
            continue
        removals.append(span)
        fired.append(rule)
    if not removals:
        return text, ()

    pieces: list[str] = []
    cursor = 0
    for span in removals:
        pieces.append(text[cursor : span.start])
        cursor = span.end
    pieces.append(text[cursor:])
    return "".join(pieces).strip(" \t　"), tuple(dict.fromkeys(fired))


def _parenthetical_rule(text: str, span: _ParentheticalSpan) -> str:
    if (
        span.start > 0
        and _BASE_CHARACTER_RE.fullmatch(text[span.start - 1])
        and _READING_RE.fullmatch(span.contents)
    ):
        return RULE_READING_GLOSS
    return RULE_PARENTHETICAL


def _top_level_parenthetical_spans(text: str) -> tuple[_ParentheticalSpan, ...]:
    spans: list[_ParentheticalSpan] = []
    stack: list[tuple[str, int]] = []
    for index, character in enumerate(text):
        if character in _PAREN_PAIRS:
            stack.append((character, index))
            continue
        if not stack or character != _PAREN_PAIRS[stack[-1][0]]:
            continue
        _opening, start = stack.pop()
        if not stack:
            spans.append(_ParentheticalSpan(start, index + 1, text[start + 1 : index]))
    return tuple(spans)
