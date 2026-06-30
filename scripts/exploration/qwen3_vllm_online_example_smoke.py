#!/usr/bin/env python
"""Minimal vLLM online forced-alignment smoke.

Exploration-only. This intentionally mirrors vLLM's upstream
`examples/pooling/token_classify/forced_alignment_online.py` request shape:

- build a prompt with explicit `<timestamp>` tokens;
- create an in-memory 16 kHz PCM WAV data URL;
- send a chat `/pooling` request with an `audio_url` object; and
- decode token-classification logits at local `<timestamp>` positions.

Run:

    cd envs/apple
    uv run ../../scripts/exploration/qwen3_vllm_online_example_smoke.py \
      --base-url http://melchior-1:8000
"""

from __future__ import annotations

import argparse
import base64
import json
import urllib.error
import urllib.request
import wave
from io import BytesIO
from pathlib import Path
from typing import Any

import numpy as np
import torch
from huggingface_hub import hf_hub_download
from transformers import AutoTokenizer


MODEL = "Qwen/Qwen3-ForcedAligner-0.6B"
RAW_CONTENT_CHAT_TEMPLATE = "{{ messages[0]['content'] }}"


def main() -> int:
    args = parse_args()
    words = args.words
    prompt = build_prompt(words)
    payload = build_payload(
        model=args.model,
        prompt=prompt,
        audio_uri=encode_silent_wav_data_uri(),
        include_chat_template=args.include_chat_template,
    )

    result = post_json(f"{args.base_url.rstrip('/')}/pooling", payload)
    tokenizer = AutoTokenizer.from_pretrained(args.model)
    timestamp_token_id, timestamp_segment_time = load_timestamp_config(args.model)

    output = result["data"][0]
    logits = torch.tensor(output["data"])
    predictions = logits.argmax(dim=-1)
    token_ids = tokenizer(prompt, add_special_tokens=False)["input_ids"]
    audio_pad_token_id = tokenizer.convert_tokens_to_ids("<|audio_pad|>")

    audio_pad_index = token_ids.index(audio_pad_token_id)
    audio_token_shift = len(predictions) - len(token_ids)
    if audio_token_shift < 0:
        raise RuntimeError(
            "Response is shorter than local prompt tokenization; check chat template."
        )

    ts_predictions = []
    for index, token_id in enumerate(token_ids):
        if token_id != timestamp_token_id:
            continue
        prediction_index = index + audio_token_shift if index > audio_pad_index else index
        ts_predictions.append(
            predictions[prediction_index].item() * timestamp_segment_time
        )

    if len(ts_predictions) < len(words) * 2:
        raise RuntimeError(
            f"Expected at least {len(words) * 2} timestamps, got {len(ts_predictions)}"
        )

    for index, word in enumerate(words):
        start_ms = ts_predictions[index * 2]
        end_ms = ts_predictions[index * 2 + 1]
        print(f"{word:15s} {start_ms / 1000:.3f}s - {end_ms / 1000:.3f}s")
    print(json.dumps({"usage": result.get("usage")}, indent=2))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://melchior-1:8000")
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--words", nargs="+", default=["Hello", "world"])
    parser.add_argument(
        "--include-chat-template",
        action="store_true",
        help="Match upstream example for servers started with trust-request-chat-template.",
    )
    return parser.parse_args()


def build_prompt(words: list[str]) -> str:
    body = "<timestamp><timestamp>".join(words) + "<timestamp><timestamp>"
    return f"<|audio_start|><|audio_pad|><|audio_end|>{body}"


def encode_silent_wav_data_uri(sample_rate: int = 16000, duration_s: int = 5) -> str:
    audio = np.zeros(sample_rate * duration_s, dtype=np.int16)
    with BytesIO() as audio_buffer:
        with wave.open(audio_buffer, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(np.dtype(np.int16).itemsize)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(audio.tobytes())
        audio_base64 = base64.b64encode(audio_buffer.getvalue()).decode("utf-8")
    return f"data:audio/wav;base64,{audio_base64}"


def build_payload(
    *,
    model: str,
    prompt: str,
    audio_uri: str,
    include_chat_template: bool,
) -> dict[str, Any]:
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "audio_url", "audio_url": {"url": audio_uri}},
                ],
            }
        ],
        "task": "token_classify",
    }
    if include_chat_template:
        payload["chat_template"] = RAW_CONTENT_CHAT_TEMPLATE
    return payload


def post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "Test Client"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {error.code}: {detail}") from error


def load_timestamp_config(model: str) -> tuple[int, float]:
    model_path = Path(model)
    config_path = (
        model_path / "config.json"
        if model_path.exists()
        else Path(hf_hub_download(repo_id=model, filename="config.json"))
    )
    config = json.loads(config_path.read_text(encoding="utf-8"))
    return config["timestamp_token_id"], config["timestamp_segment_time"]


if __name__ == "__main__":
    raise SystemExit(main())
