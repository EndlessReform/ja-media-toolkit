"""Compact HTTP client for the Qwen adapter colocated with vLLM."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from ja_media_inference.forced_alignment.text_units import (
    AlignmentToken,
    TokenAlignment,
)


@dataclass(frozen=True)
class ProfiledAlignmentCall:
    """One compact adapter result with server-measured stage timings."""

    alignments: list[TokenAlignment]
    profile: dict[str, float | int]


class Qwen3AdapterClient:
    """Send compressed-audio references and receive compact token timings."""

    name = "qwen3-adapter"
    model = "Qwen/Qwen3-ForcedAligner-0.6B"

    def __init__(
        self,
        *,
        base_url: str,
        timeout_s: float = 180.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=timeout_s, trust_env=False)

    def cache_audio(self, audio: Mapping[str, Any]) -> str:
        """Ensure one pinned canonical audio object is cached on the GPU host."""

        payload = {
            "object_bucket": audio["object_bucket"],
            "object_key": audio["object_key"],
            "sha256": audio["sha256"],
            "codec": audio["codec"],
        }
        result = self._post("/audio/cache", payload)
        return str(result["audio_id"])

    def align_crop(
        self,
        *,
        audio_id: str,
        crop_start_s: float,
        crop_end_s: float,
        tokens: Sequence[AlignmentToken],
    ) -> list[TokenAlignment]:
        """Align an episode crop while keeping the raw model output on the server."""

        return self.align_crop_profiled(
            audio_id=audio_id,
            crop_start_s=crop_start_s,
            crop_end_s=crop_end_s,
            tokens=tokens,
        ).alignments

    def align_crop_profiled(
        self,
        *,
        audio_id: str,
        crop_start_s: float,
        crop_end_s: float,
        tokens: Sequence[AlignmentToken],
    ) -> ProfiledAlignmentCall:
        """Align a crop and return the adapter's measured stage timings."""

        by_id = {token.id: token for token in tokens}
        payload = {
            "audio_id": audio_id,
            "crop_start_s": crop_start_s,
            "crop_end_s": crop_end_s,
            "tokens": [
                {
                    "id": token.id,
                    "text": token.text,
                    "group_id": token.group_id,
                    "group_index": token.group_index,
                    "char_start": token.char_start,
                    "char_end": token.char_end,
                }
                for token in tokens
            ],
        }
        result = self._post("/align", payload)
        alignments = []
        returned_ids: set[str] = set()
        for row in result["alignments"]:
            token_id = str(row["token_id"])
            if token_id not in by_id:
                raise RuntimeError(f"adapter returned unknown token ID: {token_id}")
            if token_id in returned_ids:
                raise RuntimeError(f"adapter returned duplicate token ID: {token_id}")
            returned_ids.add(token_id)
            alignments.append(
                TokenAlignment(
                    token=by_id[token_id],
                    start_s=float(row["start_s"]),
                    end_s=float(row["end_s"]),
                    confidence=(
                        float(row["confidence"])
                        if row.get("confidence") is not None
                        else None
                    ),
                    metadata=dict(row.get("metadata") or {}),
                )
            )
        if returned_ids != set(by_id):
            raise RuntimeError(
                f"adapter returned {len(alignments)} of {len(tokens)} token alignments"
            )
        return ProfiledAlignmentCall(
            alignments=alignments,
            profile={
                key: value for key, value in (result.get("profile") or {}).items()
            },
        )

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = self._client.post(f"{self.base_url}{path}", json=payload)
        if response.status_code != 200:
            raise RuntimeError(f"adapter HTTP {response.status_code}: {response.text}")
        result = response.json()
        if not isinstance(result, dict):
            raise RuntimeError("adapter response was not a JSON object")
        return result
