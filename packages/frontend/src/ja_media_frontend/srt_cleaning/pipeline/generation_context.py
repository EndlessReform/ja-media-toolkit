from __future__ import annotations

from typing import Any


def render_series_context(ctx: Any) -> str:
    """Render compact metadata context for a cleaning window prompt."""

    lines = [
        f"AniList ID: {ctx.anilist_id}",
        f"English title: {ctx.title_english or 'unknown'}",
        f"Native title: {ctx.title_native or 'unknown'}",
        f"Romaji title: {ctx.title_romaji or 'unknown'}",
    ]
    if ctx.description:
        lines.append(f"Synopsis: {ctx.description}")
    names = [name for char in ctx.characters[:30] if (name := format_character_name(char))]
    if names:
        lines.append("Characters: " + ", ".join(names))
    return "\n".join(lines)


def format_character_name(char: dict[str, Any]) -> str | None:
    """Format AniList character names with native names first for JP biasing."""

    node = char.get("node", char)
    name_info = node.get("name", {}) if isinstance(node, dict) else {}
    if not isinstance(name_info, dict):
        return None

    native = clean_optional_text(name_info.get("native"))
    full = clean_optional_text(name_info.get("full"))
    alternatives: list[str] = []
    raw_alternatives = name_info.get("alternative", ())
    if isinstance(raw_alternatives, list):
        for item in raw_alternatives:
            if alternative := clean_optional_text(item):
                alternatives.append(alternative)
    romanized = [value for value in [full, *alternatives] if value and value != native]
    if native and romanized:
        return f"{native} ({' / '.join(romanized[:2])})"
    return native or full


def clean_optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None
