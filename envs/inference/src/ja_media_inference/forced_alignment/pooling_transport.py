"""Measured HTTP transport for Qwen token-classification pooling requests."""

from __future__ import annotations

import base64
import json
import mimetypes
from pathlib import Path
import time
from typing import Any, Literal

import httpx
import numpy as np


RAW_CONTENT_CHAT_TEMPLATE = "{{ messages[0]['content'] }}"


def build_pooling_payload(
    *,
    model: str,
    prompt: str,
    audio_path: Path,
    include_chat_template: bool,
    encoding_format: Literal["float", "bytes"] = "float",
) -> dict[str, Any]:
    """Build the exact multimodal JSON body accepted by vLLM `/pooling`."""

    mime_type = mimetypes.guess_type(audio_path)[0] or "audio/wav"
    audio_b64 = base64.b64encode(audio_path.read_bytes()).decode("ascii")
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "audio_url",
                        "audio_url": {
                            "url": f"data:{mime_type};base64,{audio_b64}",
                        },
                    },
                ],
            }
        ],
        "task": "token_classify",
        "encoding_format": encoding_format,
    }
    if encoding_format == "bytes":
        payload.update(embed_dtype="float16", endianness="little")
    if include_chat_template:
        payload["chat_template"] = RAW_CONTENT_CHAT_TEMPLATE
    return payload


def post_pooling(
    url: str,
    payload: dict[str, Any],
    *,
    timeout_s: float,
) -> dict[str, Any]:
    result, _timings = post_pooling_profiled(url, payload, timeout_s=timeout_s)
    return result


def post_pooling_profiled(
    url: str,
    payload: dict[str, Any],
    *,
    timeout_s: float,
) -> tuple[dict[str, Any], dict[str, float | int]]:
    """Post once and separate headers, body transfer, and JSON parsing."""

    started = time.perf_counter()
    with httpx.Client(timeout=timeout_s) as client:
        with client.stream("POST", url, json=payload) as response:
            headers_received = time.perf_counter()
            response.read()
            body_received = time.perf_counter()
    if response.status_code != 200:
        raise RuntimeError(f"vLLM HTTP {response.status_code}: {response.text}")
    parse_started = time.perf_counter()
    result = response.json()
    parsed = time.perf_counter()
    if "data" not in result:
        raise RuntimeError(f"vLLM response did not include data: {result}")
    return result, {
        "vllm_request_to_headers_s": headers_received - started,
        "vllm_response_body_s": body_received - headers_received,
        "vllm_response_json_s": parsed - parse_started,
        "vllm_request_bytes": len(response.request.content),
        "vllm_response_bytes": len(response.content),
    }


def post_pooling_binary_profiled(
    url: str,
    payload: dict[str, Any],
    *,
    timeout_s: float,
) -> tuple[np.ndarray, dict[str, float | int]]:
    """Post once and decode one little-endian fp16 pooling matrix."""

    started = time.perf_counter()
    with httpx.Client(timeout=timeout_s, trust_env=False) as client:
        with client.stream("POST", url, json=payload) as response:
            headers_received = time.perf_counter()
            response.read()
            body_received = time.perf_counter()
    if response.status_code != 200:
        raise RuntimeError(f"vLLM HTTP {response.status_code}: {response.text}")

    decode_started = time.perf_counter()
    try:
        metadata = json.loads(response.headers["metadata"])
        items = metadata["data"]
        if len(items) != 1:
            raise ValueError(f"expected one pooling matrix, received {len(items)}")
        item = items[0]
        if item["embed_dtype"] != "float16" or item["endianness"] != "little":
            raise ValueError(
                "expected little-endian float16 pooling output, got "
                f"{item['embed_dtype']}/{item['endianness']}"
            )
        shape = tuple(int(value) for value in item["shape"])
        if len(shape) != 2:
            raise ValueError(f"expected a two-dimensional pooling matrix: {shape}")
        start, end = int(item["start"]), int(item["end"])
        matrix = np.frombuffer(response.content[start:end], dtype="<f2").reshape(shape)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"invalid binary vLLM pooling response: {exc}") from exc
    decoded = time.perf_counter()
    return matrix, {
        "vllm_request_to_headers_s": headers_received - started,
        "vllm_response_body_s": body_received - headers_received,
        "vllm_response_decode_s": decoded - decode_started,
        "vllm_request_bytes": len(response.request.content),
        "vllm_response_bytes": len(response.content),
    }
