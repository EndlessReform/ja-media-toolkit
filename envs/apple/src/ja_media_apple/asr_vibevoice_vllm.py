from __future__ import annotations

import base64
import asyncio
import io
import json
import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import librosa
import httpx
import numpy as np
import torch
from huggingface_hub import hf_hub_download
from ja_media_core.asr import (
    AsrRequest,
    AsrRuntimeOptions,
    AsrSegment,
    AsrTranscript,
)
from ja_media_core.audio import AudioChunk, materialize_audio_chunk
from safetensors import safe_open
from tqdm.asyncio import tqdm as tqdm_asyncio

if TYPE_CHECKING:
    from ja_media_apple.asr_config import VibeVoiceVllmAsrConfig


TRANSFORMERS_GIT_REV = "cbb65a4815d44f1d8b8ff7f51cca24ce491fc09e"
AUDIO_ENCODER_FILE = "audio_encoder.safetensors"
_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class VibeVoiceAudioModules:
    """Loaded audio-side model pieces needed before vLLM inference."""

    config: Any
    acoustic: torch.nn.Module
    semantic: torch.nn.Module
    projector: torch.nn.Module
    weights_path: Path
    transformers_version: str
    transformers_file: str
    load_counts: dict[str, int]


@dataclass(frozen=True)
class VibeVoiceAudioRows:
    """Projected audio rows plus timing metadata for one source chunk."""

    features: torch.Tensor
    duration_seconds: float
    sample_rate: int
    valid_samples: int


class VibeVoiceVllmAsrBackend:
    """VibeVoice audio encoder client with a remote vLLM decoder.

    This class is the first production-shaped shell around
    ``asr_mixed_embed_client.py``. It intentionally performs only lightweight
    startup work for now: config normalization and validation. The actual
    encode/post/infer path will be moved over in later slices so the PoC remains
    available for smoke-test comparison.
    """

    name = "vibevoice_vllm"

    def __init__(self, config: VibeVoiceVllmAsrConfig) -> None:
        self.config = config
        self.vllm_base_url = config.vllm_base_url.rstrip("/")
        self.vllm_model = config.vllm_model
        self.checkpoint = config.checkpoint
        self.device = config.device
        self.dtype = config.dtype
        self.timeout_s = config.timeout_s
        self.default_backend_options = config.runtime_backend_options()
        self._modules: VibeVoiceAudioModules | None = None
        if config.load_on_startup:
            self._modules = self.load_model()

    @property
    def metadata(self) -> dict[str, Any]:
        """Backend startup metadata safe to include in dry-run output."""

        return {
            "name": self.name,
            "vllm_base_url": self.vllm_base_url,
            "vllm_model": self.vllm_model,
            "checkpoint": self.checkpoint,
            "device": self.device,
            "dtype": self.dtype,
            "timeout_s": self.timeout_s,
            "target_split_s": self.config.target_split_s,
            "split_search_radius_s": self.config.split_search_radius_s,
            "rejoin_overlap_s": self.config.rejoin_overlap_s,
            "backend_options": dict(self.default_backend_options),
            "model_loaded": self._modules is not None,
            "model_metadata": self.model_metadata,
        }

    @property
    def model_metadata(self) -> dict[str, Any]:
        """Return metadata about the loaded audio-side model."""

        if self._modules is None:
            return {}
        return {
            "weights_path": str(self._modules.weights_path),
            "transformers_version": self._modules.transformers_version,
            "transformers_file": self._modules.transformers_file,
            "expected_transformers_git_rev": TRANSFORMERS_GIT_REV,
            "load_counts": dict(self._modules.load_counts),
            "sample_rate": int(getattr(self._modules.config, "sample_rate", 24_000)),
            "chunk_size": int(self._modules.config.acoustic_tokenizer_chunk_size),
            "hop_length": int(
                self._modules.config.acoustic_tokenizer_encoder_config.hop_length
            ),
        }

    def load_model(self) -> VibeVoiceAudioModules:
        """Load VibeVoice audio encoder/projector modules and strict-load weights."""

        classes = self._import_hf_classes()
        dtype = _torch_dtype(self.dtype)
        device = torch.device(self.device)
        config = classes["VibeVoiceAsrConfig"].from_pretrained(self.checkpoint)
        acoustic = classes["VibeVoiceAcousticTokenizerEncoderModel"](
            config.acoustic_tokenizer_encoder_config
        ).to(device=device, dtype=dtype).eval()
        semantic = classes["VibeVoiceAcousticTokenizerEncoderModel"](
            config.semantic_tokenizer_encoder_config
        ).to(device=device, dtype=dtype).eval()
        projector = classes["VibeVoiceAsrMultiModalProjector"](config).to(
            device=device,
            dtype=dtype,
        ).eval()

        weights_path = _resolve_audio_weights(self.checkpoint)
        acoustic_state = _load_prefixed_state(
            weights_path,
            "model.acoustic_tokenizer_encoder.",
        )
        semantic_state = _load_prefixed_state(
            weights_path,
            "model.semantic_tokenizer_encoder.",
        )
        projector_state = _load_prefixed_state(
            weights_path,
            "model.multi_modal_projector.",
        )
        load_counts = {
            "acoustic_tokenizer_encoder": _strict_load(
                acoustic,
                acoustic_state,
                "acoustic_tokenizer_encoder",
            ),
            "semantic_tokenizer_encoder": _strict_load(
                semantic,
                semantic_state,
                "semantic_tokenizer_encoder",
            ),
            "multi_modal_projector": _strict_load(
                projector,
                projector_state,
                "multi_modal_projector",
            ),
        }

        return VibeVoiceAudioModules(
            config=config,
            acoustic=acoustic,
            semantic=semantic,
            projector=projector,
            weights_path=weights_path,
            transformers_version=classes["transformers_version"],
            transformers_file=classes["transformers_file"],
            load_counts=load_counts,
        )

    def _import_hf_classes(self) -> dict[str, Any]:
        """Import only the HF VibeVoice classes needed for audio-side startup."""

        if self.config.transformers_src:
            src = str(Path(self.config.transformers_src).expanduser().resolve())
            if src not in sys.path:
                sys.path.insert(0, src)

        from transformers.models.vibevoice_acoustic_tokenizer import (
            VibeVoiceAcousticTokenizerEncoderModel,
            VibeVoiceAcousticTokenizerFeatureExtractor,
        )
        from transformers.models.vibevoice_asr import VibeVoiceAsrConfig
        from transformers.models.vibevoice_asr.modeling_vibevoice_asr import (
            VibeVoiceAsrMultiModalProjector,
        )

        import transformers

        return {
            "VibeVoiceAcousticTokenizerEncoderModel": (
                VibeVoiceAcousticTokenizerEncoderModel
            ),
            "VibeVoiceAcousticTokenizerFeatureExtractor": (
                VibeVoiceAcousticTokenizerFeatureExtractor
            ),
            "VibeVoiceAsrConfig": VibeVoiceAsrConfig,
            "VibeVoiceAsrMultiModalProjector": VibeVoiceAsrMultiModalProjector,
            "transformers_version": transformers.__version__,
            "transformers_file": transformers.__file__ or "",
        }

    def transcribe(
        self,
        request: AsrRequest,
        *,
        runtime_options: AsrRuntimeOptions | None = None,
    ) -> list[AsrTranscript]:
        """Synchronous compatibility wrapper around ``transcribe_async``."""

        return asyncio.run(
            self.transcribe_async(request, runtime_options=runtime_options)
        )

    async def transcribe_async(
        self,
        request: AsrRequest,
        *,
        runtime_options: AsrRuntimeOptions | None = None,
    ) -> list[AsrTranscript]:
        """Transcribe audio chunks with bounded async vLLM submit."""

        return await self.infer_async(request, runtime_options=runtime_options)

    def infer(
        self,
        request: AsrRequest,
        *,
        runtime_options: AsrRuntimeOptions | None = None,
    ) -> list[AsrTranscript]:
        """Synchronous compatibility wrapper around ``infer_async``."""

        return asyncio.run(self.infer_async(request, runtime_options=runtime_options))

    async def infer_async(
        self,
        request: AsrRequest,
        *,
        runtime_options: AsrRuntimeOptions | None = None,
    ) -> list[AsrTranscript]:
        """Run the VibeVoice/vLLM inference path."""

        modules = self._modules or self.load_model()
        self._modules = modules
        options = _merge_backend_options(self.default_backend_options, runtime_options)
        timeout_s = (
            runtime_options.timeout_s
            if runtime_options is not None and runtime_options.timeout_s is not None
            else self.timeout_s
        )

        max_concurrent_requests = max(
            1,
            int(options.get("max_concurrent_requests", 1)),
        )
        return await self._infer_chunks_async(
            request,
            modules=modules,
            timeout_s=timeout_s,
            max_concurrent_requests=max_concurrent_requests,
            max_output_tokens=int(options.get("max_output_tokens", 2048)),
            temperature=float(options.get("temperature", 0.0)),
            top_p=float(options.get("top_p", 1.0)),
            repetition_penalty=float(options.get("repetition_penalty", 1.1)),
            vllm_request_max_attempts=int(options.get("vllm_request_max_attempts", 3)),
            vllm_request_retry_backoff_s=float(
                options.get("vllm_request_retry_backoff_s", 1.0)
            ),
        )

    async def _infer_chunks_async(
        self,
        request: AsrRequest,
        *,
        modules: VibeVoiceAudioModules,
        timeout_s: float,
        max_concurrent_requests: int,
        max_output_tokens: int,
        temperature: float,
        top_p: float,
        repetition_penalty: float,
        vllm_request_max_attempts: int,
        vllm_request_retry_backoff_s: float,
    ) -> list[AsrTranscript]:
        submit_semaphore = asyncio.Semaphore(max_concurrent_requests)
        encode_lock = asyncio.Lock()
        timeout = httpx.Timeout(timeout_s)

        async with httpx.AsyncClient(timeout=timeout) as client:

            async def transcribe_chunk(
                index: int,
                chunk: AudioChunk,
            ) -> tuple[int, AsrTranscript]:
                async with encode_lock:
                    rows = await asyncio.to_thread(
                        self._encode_audio_rows,
                        modules,
                        chunk,
                    )
                user_text = _build_user_text(
                    rows.duration_seconds,
                    request.context,
                    request.hotwords,
                )
                payload = await asyncio.to_thread(
                    _build_chat_payload,
                    model=self.vllm_model,
                    system_prompt=self.config.system_prompt,
                    user_text=user_text,
                    audio_rows=rows.features,
                    max_tokens=max_output_tokens,
                    temperature=temperature,
                    top_p=top_p,
                    repetition_penalty=repetition_penalty,
                    stop=list(self.config.stop),
                )
                async with submit_semaphore:
                    response_json, elapsed_s = await _post_to_vllm_async(
                        client,
                        self.vllm_base_url,
                        payload,
                        max_attempts=vllm_request_max_attempts,
                        retry_backoff_s=vllm_request_retry_backoff_s,
                    )
                return index, _transcript_from_response(
                    chunk,
                    response_json,
                    elapsed_s=elapsed_s,
                    rows=rows,
                    user_text=user_text,
                    backend=self.name,
                    language=request.language,
                    vllm_base_url=self.vllm_base_url,
                    vllm_model=self.vllm_model,
                    max_concurrent_requests=max_concurrent_requests,
                )

            tasks = [
                asyncio.create_task(transcribe_chunk(index, chunk))
                for index, chunk in enumerate(request.chunks)
            ]
            completed = []
            try:
                for task in tqdm_asyncio(
                    asyncio.as_completed(tasks),
                    total=len(tasks),
                    desc="vLLM ASR",
                    unit="chunk",
                ):
                    completed.append(await task)
            except Exception:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise

        completed.sort(key=lambda item: item[0])
        return [transcript for _, transcript in completed]

    def _encode_audio_rows(
        self,
        modules: VibeVoiceAudioModules,
        chunk: AudioChunk,
    ) -> VibeVoiceAudioRows:
        """Materialize one chunk and project it into vLLM prompt-embed rows."""

        sample_rate = int(getattr(modules.config, "sample_rate", 24_000))
        hop_length = int(modules.config.acoustic_tokenizer_encoder_config.hop_length)
        materialized = materialize_audio_chunk(chunk)
        audio = _mono_audio(materialized.samples)
        if materialized.sample_rate_hz != sample_rate:
            audio = librosa.resample(
                audio,
                orig_sr=materialized.sample_rate_hz,
                target_sr=sample_rate,
            ).astype(np.float32)

        feature_extractor = self._import_hf_classes()[
            "VibeVoiceAcousticTokenizerFeatureExtractor"
        ](sampling_rate=sample_rate)
        batch = feature_extractor(
            audio,
            sampling_rate=sample_rate,
            pad_to_multiple_of=hop_length,
            return_attention_mask=True,
            return_tensors="pt",
        )
        valid_samples = int(batch["padding_mask"].sum(dim=-1)[0].item())
        input_values = batch["input_values"].to(
            device=torch.device(self.device),
            dtype=_torch_dtype(self.dtype),
        )
        padding_mask = batch["padding_mask"].to(device=torch.device(self.device))

        with torch.inference_mode():
            features = _encode_audio_features(
                acoustic=modules.acoustic,
                semantic=modules.semantic,
                projector=modules.projector,
                input_values=input_values,
                padding_mask=padding_mask,
                chunk_size=modules.config.acoustic_tokenizer_chunk_size,
                hop_length=hop_length,
                vae_std=modules.config.acoustic_tokenizer_encoder_config.vae_std,
                seed=self.config.seed,
            )

        expected_rows = int(np.ceil(valid_samples / hop_length))
        if int(features.shape[0]) != expected_rows:
            raise RuntimeError(
                f"expected {expected_rows} audio rows, got {int(features.shape[0])}"
            )

        return VibeVoiceAudioRows(
            features=features.detach().cpu().float(),
            duration_seconds=valid_samples / sample_rate,
            sample_rate=sample_rate,
            valid_samples=valid_samples,
        )


def _torch_dtype(name: str) -> torch.dtype:
    aliases = {
        "bf16": "bfloat16",
        "fp16": "float16",
        "fp32": "float32",
    }
    name = aliases.get(name, name)
    try:
        dtype = getattr(torch, name)
    except AttributeError as error:
        raise ValueError(f"Unsupported torch dtype: {name!r}") from error
    if not isinstance(dtype, torch.dtype):
        raise ValueError(f"Unsupported torch dtype: {name!r}")
    return dtype


def _resolve_audio_weights(checkpoint: str) -> Path:
    local = Path(checkpoint).expanduser()
    if local.exists():
        weights_path = local / AUDIO_ENCODER_FILE
        if not weights_path.exists():
            raise FileNotFoundError(f"Missing VibeVoice audio weights: {weights_path}")
        return weights_path
    return Path(hf_hub_download(repo_id=checkpoint, filename=AUDIO_ENCODER_FILE))


def _load_prefixed_state(weights_path: Path, prefix: str) -> dict[str, torch.Tensor]:
    state: dict[str, torch.Tensor] = {}
    with safe_open(weights_path, framework="pt", device="cpu") as handle:
        for key in handle.keys():
            if key.startswith(prefix):
                state[key.removeprefix(prefix)] = handle.get_tensor(key)
    if not state:
        raise KeyError(f"{weights_path} has no tensors under prefix {prefix!r}")
    return state


def _strict_load(
    module: torch.nn.Module,
    state: dict[str, torch.Tensor],
    label: str,
) -> int:
    expected = module.state_dict()
    missing = sorted(set(expected) - set(state))
    unexpected = sorted(set(state) - set(expected))
    wrong_shape = sorted(
        key
        for key in set(expected) & set(state)
        if tuple(expected[key].shape) != tuple(state[key].shape)
    )
    if missing or unexpected or wrong_shape:
        raise RuntimeError(
            f"{label} state mismatch: "
            f"missing={missing[:8]} unexpected={unexpected[:8]} "
            f"wrong_shape={wrong_shape[:8]}"
        )
    module.load_state_dict(state, strict=True)
    return len(state)


def _mono_audio(samples: np.ndarray) -> np.ndarray:
    if samples.ndim == 1:
        return samples.astype(np.float32)
    return samples.mean(axis=1).astype(np.float32)


def _encode_audio_features(
    *,
    acoustic: torch.nn.Module,
    semantic: torch.nn.Module,
    projector: torch.nn.Module,
    input_values: torch.Tensor,
    padding_mask: torch.Tensor,
    chunk_size: int,
    hop_length: int,
    vae_std: float,
    seed: int,
) -> torch.Tensor:
    torch.manual_seed(seed)

    acoustic_cache = None
    semantic_cache = None
    acoustic_latents = []
    semantic_latents = []
    for audio_chunk in torch.split(input_values, chunk_size, dim=-1):
        acoustic_output = acoustic(
            audio_chunk,
            padding_cache=acoustic_cache,
            use_cache=True,
        )
        semantic_output = semantic(
            audio_chunk,
            padding_cache=semantic_cache,
            use_cache=True,
        )
        acoustic_latents.append(acoustic_output.latents)
        semantic_latents.append(semantic_output.latents)
        acoustic_cache = acoustic_output.padding_cache
        semantic_cache = semantic_output.padding_cache

    acoustic_latents = torch.cat(acoustic_latents, dim=1)
    semantic_latents = torch.cat(semantic_latents, dim=1)

    noise_std = vae_std * torch.randn(
        acoustic_latents.shape[0],
        device=acoustic_latents.device,
        dtype=acoustic_latents.dtype,
    )
    acoustic_latents = acoustic_latents + noise_std[:, None, None] * torch.randn_like(
        acoustic_latents
    )
    combined = projector(acoustic_latents, semantic_latents)

    num_audio_tokens = torch.ceil(padding_mask.sum(dim=-1) / hop_length).to(torch.int64)
    feature_mask = (
        torch.arange(num_audio_tokens.max(), device=combined.device)
        < num_audio_tokens[:, None]
    )
    return combined[feature_mask]


def _tensor_to_base64(tensor: torch.Tensor) -> str:
    buffer = io.BytesIO()
    torch.save(tensor, buffer)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _build_user_text(
    duration_seconds: float,
    context_info: str | None,
    hotwords: tuple[str, ...],
) -> str:
    requested_keys = "Start time, End time, Speaker ID, Content"
    duration = f"{duration_seconds:.2f}"
    context_parts = []
    if context_info:
        context_parts.append(context_info)
    if hotwords:
        context_parts.append("Hotwords: " + ", ".join(hotwords))
    if context_parts:
        context = " ".join(context_parts)
        return (
            f"This is a {duration} seconds audio, with extra info: {context}\n\n"
            f"Please transcribe it with these keys: {requested_keys}"
        )
    return (
        f"This is a {duration} seconds audio, please transcribe it with these keys: "
        f"{requested_keys}"
    )


def _build_chat_payload(
    *,
    model: str,
    system_prompt: str,
    user_text: str,
    audio_rows: torch.Tensor,
    max_tokens: int,
    temperature: float,
    top_p: float,
    repetition_penalty: float,
    stop: list[str],
) -> dict[str, Any]:
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "prompt_embeds", "data": _tensor_to_base64(audio_rows)},
                    {"type": "text", "text": "\n" + user_text},
                ],
            },
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "top_p": top_p,
        "repetition_penalty": repetition_penalty,
        "stop": stop,
    }


async def _post_to_vllm_async(
    client: httpx.AsyncClient,
    url: str,
    payload: dict[str, Any],
    *,
    max_attempts: int = 1,
    retry_backoff_s: float = 0.0,
) -> tuple[dict[str, Any], float]:
    max_attempts = max(1, max_attempts)
    started = time.time()
    endpoint = url.rstrip("/") + "/v1/chat/completions"
    for attempt in range(1, max_attempts + 1):
        try:
            response = await client.post(endpoint, json=payload)
        except httpx.TransportError as error:
            if attempt >= max_attempts:
                raise RuntimeError(
                    "vLLM request failed after "
                    f"{max_attempts} attempt(s): {type(error).__name__}: {error}"
                ) from error
            _LOG.warning(
                "Retrying vLLM request after %s on attempt %s/%s",
                type(error).__name__,
                attempt,
                max_attempts,
            )
            await asyncio.sleep(retry_backoff_s)
            continue

        elapsed = time.time() - started
        if response.status_code >= 500 and attempt < max_attempts:
            _LOG.warning(
                "Retrying vLLM request after HTTP %s on attempt %s/%s",
                response.status_code,
                attempt,
                max_attempts,
            )
            await asyncio.sleep(retry_backoff_s)
            continue

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            raise RuntimeError(
                f"vLLM request failed: HTTP {response.status_code}: {response.text}"
            ) from error
        return response.json(), elapsed

    raise AssertionError("unreachable vLLM request retry state")


def _transcript_from_response(
    chunk: AudioChunk,
    response_json: dict[str, Any],
    *,
    elapsed_s: float,
    rows: VibeVoiceAudioRows,
    user_text: str,
    backend: str,
    language: str | None,
    vllm_base_url: str,
    vllm_model: str,
    max_concurrent_requests: int,
) -> AsrTranscript:
    text = _response_text(response_json)
    return AsrTranscript(
        chunk=chunk,
        text=text,
        segments=tuple(_parse_segments(text, chunk)),
        backend=backend,
        language=language,
        metadata={
            "vllm_base_url": vllm_base_url,
            "vllm_model": vllm_model,
            "elapsed_s": elapsed_s,
            "usage": response_json.get("usage"),
            "audio_rows_shape": tuple(rows.features.shape),
            "audio_duration_seconds": rows.duration_seconds,
            "valid_samples": rows.valid_samples,
            "user_text": user_text,
            "max_concurrent_requests": max_concurrent_requests,
            "raw_response": response_json,
        },
    )


def _response_text(response_json: dict[str, Any]) -> str:
    choices = response_json.get("choices") or []
    if not choices:
        return ""
    return (choices[0].get("message") or {}).get("content") or ""


def _parse_segments(text: str, chunk: AudioChunk) -> list[AsrSegment]:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return []

    if isinstance(parsed, dict):
        items = parsed.get("segments") or parsed.get("transcript") or []
    else:
        items = parsed
    if not isinstance(items, list):
        return []

    segments: list[AsrSegment] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        start = _coerce_time(
            _first_present(item, "Start time", "Start", "start", "start_s")
        )
        end = _coerce_time(
            _first_present(item, "End time", "End", "end", "end_s")
        )
        content = _first_present(item, "Content", "text", "content")
        if start is None or end is None or not isinstance(content, str):
            continue
        start_s = chunk.start_s + start if start < chunk.start_s else start
        end_s = chunk.start_s + end if end <= chunk.duration_s else end
        start_s = max(chunk.start_s, start_s)
        end_s = min(chunk.end_s, end_s)
        if end_s <= start_s:
            continue
        segments.append(
            AsrSegment(
                chunk=chunk,
                start_s=start_s,
                end_s=end_s,
                text=content,
                speaker=_coerce_speaker(
                    _first_present(item, "Speaker ID", "Speaker", "speaker")
                ),
                metadata={"raw": item},
            )
        )
    return segments


def _coerce_time(value: Any) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped:
        return None
    try:
        return float(stripped.rstrip("s"))
    except ValueError:
        return None


def _coerce_speaker(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _first_present(item: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in item:
            return item[key]
    return None


def _merge_backend_options(
    defaults: dict[str, Any],
    runtime_options: AsrRuntimeOptions | None,
) -> dict[str, Any]:
    options = dict(defaults)
    if runtime_options is not None:
        options.update(runtime_options.backend_options)
    return options
