from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
import time
from typing import Any, Literal, Sequence

from huggingface_hub import hf_hub_download
from transformers import AutoTokenizer

from ja_media_inference.forced_alignment.audio_probe import (
    audio_edge_distance,
    probe_audio_duration,
)
from ja_media_inference.forced_alignment.pooling_transport import (
    build_pooling_payload,
    post_pooling_profiled,
)
from ja_media_inference.forced_alignment.text_units import (
    AlignmentToken,
    TokenAlignment,
)


PromptLayout = Literal["after-token", "wrap-token"]
PROMPT_PREFIX = "<|audio_start|><|audio_pad|><|audio_end|>"


@dataclass(frozen=True)
class PromptPlan:
    """Prompt plus the token order used to interpret timestamp predictions."""

    prompt: str
    tokens: tuple[AlignmentToken, ...]
    layout: PromptLayout


@dataclass(frozen=True)
class ProfiledAlignment:
    """Token timings plus measured stages for one vLLM request."""

    alignments: list[TokenAlignment]
    timings: dict[str, float | int]


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
        return self.align_tokens_profiled(
            audio_path=audio_path, tokens=tokens
        ).alignments

    def align_tokens_profiled(
        self,
        *,
        audio_path: str | Path,
        tokens: Sequence[AlignmentToken],
    ) -> ProfiledAlignment:
        """Align tokens and measure request construction, vLLM, and reduction."""

        started = time.perf_counter()
        plan = build_prompt_plan(tokens, layout=self.prompt_layout)
        payload = build_pooling_payload(
            model=self.model,
            prompt=plan.prompt,
            audio_path=Path(audio_path),
            include_chat_template=self.trust_request_chat_template,
        )
        payload_ready = time.perf_counter()
        response, upstream_timings = post_pooling_profiled(
            f"{self.base_url}/pooling",
            payload,
            timeout_s=self.timeout_s,
        )
        upstream_done = time.perf_counter()
        alignments = self.extract_token_alignments(
            plan=plan,
            pooling_json=response,
            audio_duration_s=probe_audio_duration(Path(audio_path)),
        )
        finished = time.perf_counter()
        logits = response["data"][0]["data"]
        return ProfiledAlignment(
            alignments=alignments,
            timings={
                "prompt_and_audio_payload_s": payload_ready - started,
                **upstream_timings,
                "timestamp_reduction_s": finished - upstream_done,
                "aligner_total_s": finished - started,
                "vllm_output_rows": len(logits),
                "vllm_classes_per_row": len(logits[0]) if logits else 0,
            },
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
                local_i + audio_token_shift if local_i > audio_pad_index else local_i
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
                    "edge_distance_s": audio_edge_distance(time_s, audio_duration_s),
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
    already_probabilities = all(float(value) >= 0 for value in values) and math.isclose(
        total_input, 1.0, rel_tol=1e-3, abs_tol=1e-3
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
        "normalized_entropy": entropy / math.log(len(values))
        if len(values) > 1
        else 0.0,
    }
