from __future__ import annotations

import base64
import json
import math
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Sequence

import httpx
from huggingface_hub import hf_hub_download
from transformers import AutoTokenizer

from ja_media_inference.forced_alignment.audio_probe import (
    audio_edge_distance,
    probe_audio_duration,
)
from ja_media_inference.forced_alignment.text_units import (
    AlignmentToken,
    TokenAlignment,
)


PromptLayout = Literal["after-token", "wrap-token"]
PROMPT_PREFIX = "<|audio_start|><|audio_pad|><|audio_end|>"
RAW_CONTENT_CHAT_TEMPLATE = "{{ messages[0]['content'] }}"


@dataclass(frozen=True)
class PromptPlan:
    """Prompt plus the token order used to interpret timestamp predictions."""

    prompt: str
    tokens: tuple[AlignmentToken, ...]
    layout: PromptLayout


class Qwen3VllmForcedAligner:
    """HTTP adapter for Qwen3-ForcedAligner behind vLLM `/pooling`."""

    name = "qwen3-vllm"

    def __init__(
        self,
        *,
        base_url: str,
        model: str = "Qwen/Qwen3-ForcedAligner-0.6B",
        prompt_layout: PromptLayout = "after-token",
        timeout_s: float = 180.0,
        trust_request_chat_template: bool = False,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.prompt_layout = prompt_layout
        self.timeout_s = timeout_s
        self.trust_request_chat_template = trust_request_chat_template
        self._tokenizer: Any | None = None
        self._timestamp_config: tuple[int, float] | None = None

    def align_tokens(
        self,
        *,
        audio_path: str | Path,
        tokens: Sequence[AlignmentToken],
    ) -> list[TokenAlignment]:
        plan = build_prompt_plan(tokens, layout=self.prompt_layout)
        payload = build_pooling_payload(
            model=self.model,
            prompt=plan.prompt,
            audio_path=Path(audio_path),
            include_chat_template=self.trust_request_chat_template,
        )
        response = post_pooling(
            f"{self.base_url}/pooling",
            payload,
            timeout_s=self.timeout_s,
        )
        return self.extract_token_alignments(
            plan=plan,
            pooling_json=response,
            audio_duration_s=probe_audio_duration(Path(audio_path)),
        )

    def extract_token_alignments(
        self,
        *,
        plan: PromptPlan,
        pooling_json: dict[str, Any],
        audio_duration_s: float,
    ) -> list[TokenAlignment]:
        tokenizer = self._load_tokenizer()
        timestamp_token_id, timestamp_segment_time = self._load_timestamp_config()
        logits = pooling_json["data"][0]["data"]
        local_ids = tokenizer(plan.prompt, add_special_tokens=False)["input_ids"]
        audio_pad_token_id = tokenizer.convert_tokens_to_ids("<|audio_pad|>")
        try:
            audio_pad_index = local_ids.index(audio_pad_token_id)
        except ValueError as exc:
            raise RuntimeError("Prompt does not contain the audio pad token") from exc

        audio_token_shift = len(logits) - len(local_ids)
        if audio_token_shift < 0:
            raise RuntimeError(
                "vLLM returned fewer logit rows than local prompt tokens; "
                "check the server chat template."
            )

        timestamp_predictions: list[dict[str, float]] = []
        for local_i, token_id in enumerate(local_ids):
            if token_id != timestamp_token_id:
                continue
            server_i = (
                local_i + audio_token_shift
                if local_i > audio_pad_index
                else local_i
            )
            if server_i < 0 or server_i >= len(logits):
                raise RuntimeError(
                    f"Timestamp row {server_i} is outside logits length {len(logits)}"
                )
            row_metrics = _distribution_metrics(logits[server_i])
            time_s = row_metrics["argmax_index"] * timestamp_segment_time / 1000
            timestamp_predictions.append(
                {
                    **row_metrics,
                    "time_s": time_s,
                    "distribution_edge_distance_s": min(
                        row_metrics["argmax_index"],
                        len(logits[server_i]) - 1 - row_metrics["argmax_index"],
                    )
                    * timestamp_segment_time
                    / 1000,
                    "edge_distance_s": audio_edge_distance(
                        time_s, audio_duration_s
                    ),
                }
            )

        expected = len(plan.tokens) * 2
        if len(timestamp_predictions) != expected:
            raise RuntimeError(
                f"Expected {expected} timestamps, got {len(timestamp_predictions)}"
            )

        alignments: list[TokenAlignment] = []
        for index, token in enumerate(plan.tokens):
            start = timestamp_predictions[index * 2]
            end = timestamp_predictions[index * 2 + 1]
            alignments.append(
                TokenAlignment(
                    token=token,
                    start_s=start["time_s"],
                    end_s=end["time_s"],
                    confidence=min(start["max_probability"], end["max_probability"]),
                    metadata={
                        "prompt_layout": plan.layout,
                        "start_distribution": start,
                        "end_distribution": end,
                    },
                )
            )
        return alignments

    def _load_tokenizer(self) -> Any:
        if self._tokenizer is None:
            self._tokenizer = AutoTokenizer.from_pretrained(self.model)
        return self._tokenizer

    def _load_timestamp_config(self) -> tuple[int, float]:
        if self._timestamp_config is None:
            self._timestamp_config = load_timestamp_config(self.model)
        return self._timestamp_config


def build_prompt_plan(
    tokens: Sequence[AlignmentToken],
    *,
    layout: PromptLayout,
) -> PromptPlan:
    if layout == "after-token":
        body = "".join(f"{token.text}<timestamp><timestamp>" for token in tokens)
    elif layout == "wrap-token":
        body = "".join(f"<timestamp>{token.text}<timestamp>" for token in tokens)
    else:
        raise ValueError(f"Unsupported prompt layout: {layout}")
    return PromptPlan(
        prompt=PROMPT_PREFIX + body,
        tokens=tuple(tokens),
        layout=layout,
    )


def build_pooling_payload(
    *,
    model: str,
    prompt: str,
    audio_path: Path,
    include_chat_template: bool,
) -> dict[str, Any]:
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
    }
    if include_chat_template:
        payload["chat_template"] = RAW_CONTENT_CHAT_TEMPLATE
    return payload


def post_pooling(
    url: str,
    payload: dict[str, Any],
    *,
    timeout_s: float,
) -> dict[str, Any]:
    with httpx.Client(timeout=timeout_s) as client:
        response = client.post(url, json=payload)
    if response.status_code != 200:
        raise RuntimeError(f"vLLM HTTP {response.status_code}: {response.text}")
    result = response.json()
    if "data" not in result:
        raise RuntimeError(f"vLLM response did not include data: {result}")
    return result


def load_timestamp_config(model: str) -> tuple[int, float]:
    model_path = Path(model)
    config_path = (
        model_path / "config.json"
        if model_path.exists()
        else Path(hf_hub_download(repo_id=model, filename="config.json"))
    )
    config = json.loads(config_path.read_text(encoding="utf-8"))
    return config["timestamp_token_id"], config["timestamp_segment_time"]


def _argmax(values: Sequence[float]) -> int:
    best_i = 0
    best_value = values[0]
    for index, value in enumerate(values[1:], start=1):
        if value > best_value:
            best_i = index
            best_value = value
    return best_i


def _distribution_metrics(values: Sequence[float]) -> dict[str, Any]:
    """Summarize one timestamp distribution without claiming model calibration."""

    if not values:
        raise ValueError("timestamp logit row is empty")
    best_i = _argmax(values)
    best = float(values[best_i])
    total_input = sum(float(value) for value in values)
    already_probabilities = (
        all(float(value) >= 0 for value in values)
        and math.isclose(total_input, 1.0, rel_tol=1e-3, abs_tol=1e-3)
    )
    if already_probabilities:
        probabilities = [float(value) / total_input for value in values]
    else:
        weights = [math.exp(float(value) - best) for value in values]
        total = sum(weights)
        probabilities = [weight / total for weight in weights]
    sorted_probabilities = sorted(probabilities, reverse=True)
    entropy = -sum(
        probability * math.log(probability)
        for probability in probabilities
        if probability > 0
    )
    return {
        "argmax_index": float(best_i),
        "distribution_kind": "probabilities" if already_probabilities else "logits",
        "max_probability": probabilities[best_i],
        "top_two_probability_margin": (
            sorted_probabilities[0] - sorted_probabilities[1]
            if len(sorted_probabilities) > 1
            else sorted_probabilities[0]
        ),
        "normalized_entropy": entropy / math.log(len(values)) if len(values) > 1 else 0.0,
    }
