"""Interpret token-classification rows returned by vLLM pooling."""

from __future__ import annotations

from typing import Sequence


def select_timestamp_rows(
    *,
    logits: Sequence[Sequence[float]],
    local_ids: Sequence[int],
    timestamp_token_id: int,
    audio_pad_token_id: int,
) -> list[Sequence[float]]:
    """Accept either stock ALL output or server-side STEP timestamp rows."""

    timestamp_positions = [
        index for index, token_id in enumerate(local_ids) if token_id == timestamp_token_id
    ]
    if len(logits) == len(timestamp_positions):
        return list(logits)

    try:
        audio_pad_index = local_ids.index(audio_pad_token_id)
    except ValueError as exc:
        raise RuntimeError("Prompt does not contain the audio pad token") from exc

    audio_token_shift = len(logits) - len(local_ids)
    if audio_token_shift < 0:
        raise RuntimeError(
            "vLLM returned neither all prompt rows nor only timestamp rows"
        )

    rows: list[Sequence[float]] = []
    for local_i in timestamp_positions:
        server_i = local_i + audio_token_shift if local_i > audio_pad_index else local_i
        if server_i < 0 or server_i >= len(logits):
            raise RuntimeError(
                f"Timestamp row {server_i} is outside logits length {len(logits)}"
            )
        rows.append(logits[server_i])
    return rows
