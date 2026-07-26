"""Small value normalizers shared by Phase 0 acquisition."""


def positive_episode_number(value: object) -> int | None:
    """Accept positive integer episode fields without accepting fractions."""

    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.isdigit():
        parsed = int(value)
        return parsed if parsed > 0 else None
    return None
