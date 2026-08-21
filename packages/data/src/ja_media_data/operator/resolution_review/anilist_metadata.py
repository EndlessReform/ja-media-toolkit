"""Small model-facing normalization for AniList metadata."""

from __future__ import annotations

import html
import re


MAX_SYNOPSIS_CHARS = 2_000


def synopsis_text(description_html: object) -> str | None:
    """Return a bounded plain-text synopsis from AniList's small HTML subset."""

    if not isinstance(description_html, str) or not description_html.strip():
        return None
    text = re.sub(r"(?i)<br\s*/?>", "\n", description_html)
    text = re.sub(r"(?i)</p\s*>", "\n\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text).replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        return None
    if len(text) <= MAX_SYNOPSIS_CHARS:
        return text
    return text[: MAX_SYNOPSIS_CHARS - 1].rstrip() + "…"
