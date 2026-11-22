"""Model-agnostic forced-alignment client boundary for retiming campaigns."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, Sequence

from ja_media_inference.forced_alignment.qwen3_adapter_client import (
    Qwen3AdapterClient,
)
from ja_media_inference.forced_alignment.text_units import (
    AlignmentToken,
    TokenAlignment,
)


class ForcedAlignerClient(Protocol):
    """HTTP client contract for any forced-alignment adapter service."""

    def ensure_cached_audio(
        self,
        *,
        sha256: str,
        s3_key: str,
        bucket: str | None = None,
        local_path: Path | None = None,
    ) -> Any: ...

    def align_crop(
        self,
        *,
        audio_id: str,
        start_s: float,
        end_s: float,
        tokens: Sequence[AlignmentToken],
    ) -> list[TokenAlignment]: ...


def build_aligner_client(*, base_url: str) -> Qwen3AdapterClient:
    """Instantiate the configured HTTP forced-alignment adapter client."""

    return Qwen3AdapterClient(base_url=base_url)
