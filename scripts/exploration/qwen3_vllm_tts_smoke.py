#!/usr/bin/env python
"""Smoke Qwen3 forced alignment against a known OpenAI TTS fixture.

Exploration-only script. This is intentionally not a client SDK:

- it creates one short Japanese WAV with OpenAI TTS;
- manually constructs Qwen3 `<timestamp>` spans from known ground-truth text;
- calls a running vLLM `/pooling` server;
- right-aligns local timestamp token positions to returned pooling logits; and
- writes enough JSON output to understand what failed.

Run from an environment that has `transformers`, for example:

    cd envs/apple
    uv run ../../scripts/exploration/qwen3_vllm_tts_smoke.py
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import wave
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from transformers import AutoConfig, AutoTokenizer


DEFAULT_MODEL = "Qwen/Qwen3-ForcedAligner-0.6B"
DEFAULT_OUT_DIR = Path("scripts/exploration/out/qwen3-vllm-tts-smoke")
PROMPT_PREFIX = "<|audio_start|><|audio_pad|><|audio_end|>"

SPANS = (
    "今夜、月の裏側に小さなラーメン屋が開店しました。",
    "店主は古い炊飯器のロボットで、湯切りの音だけで天気を占います。",
    "最初のお客さんは迷子の宇宙飛行士で、注文したのは味噌ラーメンと地球の思い出です。",
    "スープを一口飲むと、彼は小学校の帰り道に見た夕焼けを思い出しました。",
    "すると窓の外で星たちが拍手して、替え玉の券が流れ星みたいに降ってきました。",
    "最後に店主は小さな声で言いました。",
    "また迷ったら、ここにおいで。宇宙で一番あたたかい席を空けておくから。",
)


@dataclass(frozen=True)
class SpanPrediction:
    index: int
    text: str
    start_s: float
    end_s: float
    duration_s: float
    status: str


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[2]
    out_dir = (repo_root / args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    load_dotenv(repo_root / ".env")
    raw_audio_path = out_dir / "openai-tts-raw.wav"
    audio_path = (
        args.audio_path.resolve()
        if args.audio_path is not None
        else out_dir / "openai-tts-ground-truth.wav"
    )
    response_path = out_dir / "pooling-response.json"
    report_path = out_dir / "alignment-report.json"

    spans = SPANS[: args.span_count] if args.span_count else SPANS
    text = "".join(spans)
    if args.skip_tts and audio_path.exists():
        print(f"Reusing existing audio: {audio_path}")
    elif args.audio_path is not None:
        raise RuntimeError(f"Audio path does not exist: {audio_path}")
    else:
        print("Generating OpenAI TTS WAV...")
        wav_bytes = create_openai_tts_wav(text)
        raw_audio_path.write_bytes(wav_bytes)
        normalize_wav(raw_audio_path, audio_path)
    duration_s = wav_duration_s(audio_path)
    print(f"Audio: {audio_path} ({duration_s:.2f}s)")

    prompt = build_prompt(spans)
    pooling_payload = build_pooling_payload(
        model=args.model,
        prompt=prompt,
        audio_path=audio_path,
        audio_part=args.audio_part,
    )
    print(f"Calling vLLM pooling server: {args.base_url}")
    started = time.monotonic()
    pooling_json = post_json(f"{args.base_url.rstrip('/')}/pooling", pooling_payload)
    elapsed_s = time.monotonic() - started
    response_path.write_text(
        json.dumps(pooling_json, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    predictions = extract_predictions(
        model=args.model,
        prompt=prompt,
        pooling_json=pooling_json,
        spans=spans,
    )
    diagnostics = diagnose_predictions(predictions, duration_s)
    report = {
        "model": args.model,
        "base_url": args.base_url,
        "audio_path": str(audio_path),
        "audio_duration_s": duration_s,
        "elapsed_s": elapsed_s,
        "prompt": prompt,
        "ground_truth_text": text,
        "spans": list(spans),
        "predictions": [asdict(item) for item in predictions],
        "diagnostics": diagnostics,
        "pooling_usage": pooling_json.get("usage"),
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"Pooling response: {response_path}")
    print(f"Report: {report_path}")
    for prediction in predictions:
        print(
            f"{prediction.index:02d} "
            f"{prediction.start_s:7.3f}-{prediction.end_s:7.3f}s "
            f"{prediction.status:12s} {prediction.text}"
        )
    if diagnostics:
        print("Diagnostics:")
        for item in diagnostics:
            print(f"- {item['code']}: {item['message']}")
    return 0 if not any(item["severity"] == "error" for item in diagnostics) else 2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://melchior-1:8000")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--audio-path", type=Path)
    parser.add_argument("--span-count", type=int)
    parser.add_argument(
        "--audio-part",
        choices=("input_audio", "audio_url_object", "audio_url_string"),
        default="input_audio",
    )
    parser.add_argument(
        "--skip-tts",
        action="store_true",
        help="Reuse the existing WAV if present instead of spending a TTS call.",
    )
    return parser.parse_args()


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key in os.environ:
            continue
        os.environ[key] = value.strip().strip("'\"")


def create_openai_tts_wav(text: str) -> bytes:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required")
    payload = {
        "model": "gpt-4o-mini-tts",
        "voice": "marin",
        "input": text,
        "instructions": (
            "Speak in clear, lively Japanese with natural pauses, like a "
            "radio narrator telling a strange but warm bedtime story. Aim for "
            "about thirty seconds."
        ),
        "response_format": "wav",
    }
    request = urllib.request.Request(
        "https://api.openai.com/v1/audio/speech",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    return request_bytes(request)


def request_bytes(request: urllib.request.Request) -> bytes:
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {error.code} from {request.full_url}: {detail}") from error


def wav_duration_s(path: Path) -> float:
    if path.suffix.lower() != ".wav":
        return media_duration_s(path)
    with wave.open(str(path), "rb") as wav_file:
        return wav_file.getnframes() / wav_file.getframerate()


def media_duration_s(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    )
    return float(result.stdout.strip())


def normalize_wav(input_path: Path, output_path: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(input_path),
            "-acodec",
            "pcm_s16le",
            "-ac",
            "1",
            "-ar",
            "24000",
            str(output_path),
        ],
        check=True,
    )


def build_prompt(spans: tuple[str, ...]) -> str:
    return PROMPT_PREFIX + "".join(f"{span}<timestamp><timestamp>" for span in spans)


def build_pooling_payload(
    *,
    model: str,
    prompt: str,
    audio_path: Path,
    audio_part: str,
) -> dict[str, Any]:
    audio_b64 = base64.b64encode(audio_path.read_bytes()).decode("ascii")
    _audio_format, media_type = audio_format_for_path(audio_path)
    audio_url = f"data:{media_type};base64,{audio_b64}"
    audio_content = {"type": "audio_url", "audio_url": {"url": audio_url}}
    return {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    audio_content,
                ],
            }
        ],
        "task": "token_classify",
    }


def audio_format_for_path(path: Path) -> tuple[str, str]:
    suffix = path.suffix.lower()
    if suffix == ".mp3":
        return "mp3", "audio/mpeg"
    return "wav", "audio/wav"


def post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    return json.loads(request_bytes(request).decode("utf-8"))


def extract_predictions(
    *,
    model: str,
    prompt: str,
    pooling_json: dict[str, Any],
    spans: tuple[str, ...],
) -> list[SpanPrediction]:
    tokenizer = AutoTokenizer.from_pretrained(model)
    config = AutoConfig.from_pretrained(model)
    timestamp_token_id = config.timestamp_token_id
    timestamp_segment_time = config.timestamp_segment_time
    logits = pooling_json["data"][0]["data"]
    local_ids = tokenizer(prompt, add_special_tokens=False)["input_ids"]
    timestamp_s: list[float] = []

    for local_i, token_id in enumerate(local_ids):
        if token_id != timestamp_token_id:
            continue
        server_i = len(logits) - (len(local_ids) - local_i)
        if server_i < 0 or server_i >= len(logits):
            raise RuntimeError(
                f"Timestamp row {server_i} is outside logits length {len(logits)}"
            )
        predicted_bucket = argmax(logits[server_i])
        timestamp_s.append(predicted_bucket * timestamp_segment_time / 1000)

    expected = len(spans) * 2
    if len(timestamp_s) != expected:
        raise RuntimeError(f"Expected {expected} timestamps, got {len(timestamp_s)}")

    predictions = []
    for index, span in enumerate(spans):
        start_s = timestamp_s[index * 2]
        end_s = timestamp_s[index * 2 + 1]
        status = "aligned" if end_s >= start_s else "non_monotonic"
        predictions.append(
            SpanPrediction(
                index=index + 1,
                text=span,
                start_s=start_s,
                end_s=end_s,
                duration_s=end_s - start_s,
                status=status,
            )
        )
    return predictions


def argmax(values: list[float]) -> int:
    best_i = 0
    best_value = values[0]
    for index, value in enumerate(values[1:], start=1):
        if value > best_value:
            best_i = index
            best_value = value
    return best_i


def diagnose_predictions(
    predictions: list[SpanPrediction],
    audio_duration_s: float,
) -> list[dict[str, object]]:
    diagnostics: list[dict[str, object]] = []
    previous_end = -1.0
    for prediction in predictions:
        if prediction.start_s < previous_end - 0.5:
            diagnostics.append(
                {
                    "severity": "warning",
                    "code": "timestamp_went_backward",
                    "message": f"Span {prediction.index} starts before prior span ended.",
                }
            )
        if prediction.end_s > audio_duration_s + 2:
            diagnostics.append(
                {
                    "severity": "error",
                    "code": "timestamp_after_audio",
                    "message": f"Span {prediction.index} ends after the audio duration.",
                }
            )
        if prediction.status != "aligned":
            diagnostics.append(
                {
                    "severity": "error",
                    "code": "non_monotonic_span",
                    "message": f"Span {prediction.index} end is before start.",
                }
            )
        previous_end = max(previous_end, prediction.end_s)
    return diagnostics


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
