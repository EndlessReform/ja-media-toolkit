"""FastAPI adapter that keeps raw Qwen pooling tensors on the GPU host."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Protocol, Sequence

from fastapi import FastAPI, HTTPException, Response
import httpx

from ja_media_inference.forced_alignment.adapter_audio import (
    AdapterSettings,
    AudioCache,
    S3AudioObjectStore,
    decoded_crop,
)
from ja_media_inference.forced_alignment.adapter_contracts import (
    AlignmentRequest,
    AlignmentResponse,
    AudioCacheRequest,
    AudioCacheResponse,
    HealthResponse,
    TokenAlignmentResponse,
)
from ja_media_inference.forced_alignment.qwen3_vllm import Qwen3VllmForcedAligner
from ja_media_inference.forced_alignment.text_units import (
    AlignmentToken,
    TokenAlignment,
)


class TokenAligner(Protocol):
    """Model operation used after the adapter has decoded the audio crop."""

    def align_tokens(
        self, *, audio_path: str | Path, tokens: Sequence[AlignmentToken]
    ) -> list[TokenAlignment]: ...


def create_app(
    *,
    cache: AudioCache,
    aligner: TokenAligner,
    settings: AdapterSettings,
    upstream_ready: Callable[[], bool] = lambda: True,
) -> FastAPI:
    """Build an injectable app so all CPU-side behavior is locally testable."""

    app = FastAPI(title="Qwen3 forced-alignment adapter", version="0.1.0")

    @app.get("/healthz", response_model=HealthResponse)
    def health(response: Response) -> HealthResponse:
        cache.root.mkdir(parents=True, exist_ok=True)
        ready = upstream_ready()
        if not ready:
            response.status_code = 503
        return HealthResponse(
            status="ok" if ready else "vllm-unavailable",
            vllm_base_url=settings.vllm_base_url,
            audio_cache_dir=str(cache.root),
        )

    @app.post("/audio/cache", response_model=AudioCacheResponse)
    def cache_audio(request: AudioCacheRequest) -> AudioCacheResponse:
        try:
            path, already_cached = cache.ensure(request)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except (OSError, RuntimeError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return AudioCacheResponse(
            audio_id=request.sha256,
            cached=already_cached,
            byte_count=path.stat().st_size,
        )

    @app.post("/align", response_model=AlignmentResponse)
    def align(request: AlignmentRequest) -> AlignmentResponse:
        try:
            source = cache.resolve(request.audio_id)
            tokens = [_token_from_request(token) for token in request.tokens]
            with decoded_crop(
                source, start_s=request.crop_start_s, end_s=request.crop_end_s
            ) as crop:
                results = aligner.align_tokens(audio_path=crop, tokens=tokens)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (OSError, RuntimeError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return AlignmentResponse(
            audio_id=request.audio_id,
            crop_start_s=request.crop_start_s,
            crop_end_s=request.crop_end_s,
            alignments=[
                TokenAlignmentResponse(
                    token_id=result.token.id,
                    start_s=result.start_s,
                    end_s=result.end_s,
                    confidence=result.confidence,
                    metadata=result.metadata,
                )
                for result in results
            ],
        )

    return app


def create_configured_app() -> FastAPI:
    """Build the production app from process environment settings."""

    settings = AdapterSettings.from_environment()
    cache = AudioCache(
        root=settings.audio_cache_dir,
        allowed_bucket=settings.bronze_bucket,
        allowed_prefix=settings.bronze_prefix,
        store=S3AudioObjectStore(settings),
    )
    health_client = httpx.Client(timeout=2.0, trust_env=False)
    return create_app(
        cache=cache,
        aligner=Qwen3VllmForcedAligner(base_url=settings.vllm_base_url),
        settings=settings,
        upstream_ready=lambda: _upstream_ready(health_client, settings.vllm_base_url),
    )


def _token_from_request(token) -> AlignmentToken:  # type: ignore[no-untyped-def]
    return AlignmentToken(
        id=token.id,
        text=token.text,
        group_id=token.group_id,
        group_index=token.group_index,
        char_start=token.char_start,
        char_end=token.char_end,
    )


def _upstream_ready(client: httpx.Client, base_url: str) -> bool:
    try:
        return client.get(f"{base_url}/health").status_code == 200
    except httpx.HTTPError:
        return False
