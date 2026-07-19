"""Automatic episode-resolution product."""

from ja_media_data.products.episode_resolution.compiler import (
    ResolutionBatchResult,
    resolve_batch,
    resolve_document,
)
from ja_media_data.products.episode_resolution.planning import ResolutionResult

__all__ = ["ResolutionBatchResult", "ResolutionResult", "resolve_batch", "resolve_document"]
