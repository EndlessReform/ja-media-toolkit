#!/usr/bin/env python
"""Check Qwen3 vLLM payload audio before blaming the server.

Exploration-only. This reproduces the important local part of vLLM's media
load chain:

    chat payload audio_url -> data URL parse -> base64 decode -> BytesIO
    -> soundfile first -> PyAV fallback

It does not call a vLLM server. Its job is to prove whether the payload bytes
we are constructing are valid inputs to the same media-loader boundary that
the server traceback reached.

Run:

    cd envs/apple
    uv run ../../scripts/exploration/qwen3_payload_audio_chain_check.py
"""

from __future__ import annotations

import argparse
import base64
import json
import wave
from io import BytesIO
from pathlib import Path
from typing import Any


MODEL = "Qwen/Qwen3-ForcedAligner-0.6B"
PROMPT = (
    "<|audio_start|><|audio_pad|><|audio_end|>"
    "Hello<timestamp><timestamp>world<timestamp><timestamp>"
)
DEFAULT_FIXTURE_DIR = Path("scripts/exploration/out/qwen3-vllm-tts-smoke")


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[2]
    fixture_dir = (repo_root / args.fixture_dir).resolve()

    cases = [("upstream_silent_wav", build_payload(silent_wav_data_url()))]
    for path in fixture_paths(fixture_dir):
        cases.append((path.name, build_payload(file_data_url(path))))

    report = [check_payload_audio(name, payload) for name, payload in cases]
    print(json.dumps(report, ensure_ascii=False, indent=2))

    failed = [
        item
        for item in report
        if not item["stdlib_wave"]["ok"] and not item["soundfile"]["ok"]
    ]
    return 1 if failed else 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture-dir", type=Path, default=DEFAULT_FIXTURE_DIR)
    return parser.parse_args()


def fixture_paths(fixture_dir: Path) -> list[Path]:
    if not fixture_dir.exists():
        return []
    names = [
        "openai-tts-raw.wav",
        "openai-tts-ground-truth.wav",
        "openai-tts-ground-truth-24s.wav",
        "openai-tts-ground-truth-24s.mp3",
    ]
    return [fixture_dir / name for name in names if (fixture_dir / name).exists()]


def build_payload(audio_url: str) -> dict[str, Any]:
    return {
        "model": MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": PROMPT},
                    {"type": "audio_url", "audio_url": {"url": audio_url}},
                ],
            }
        ],
        "task": "token_classify",
    }


def silent_wav_data_url(sample_rate: int = 16000, duration_s: int = 5) -> str:
    samples = b"\x00\x00" * sample_rate * duration_s
    with BytesIO() as audio_buffer:
        with wave.open(audio_buffer, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(samples)
        return as_data_url("audio/wav", audio_buffer.getvalue())


def file_data_url(path: Path) -> str:
    media_type = "audio/mpeg" if path.suffix.lower() == ".mp3" else "audio/wav"
    return as_data_url(media_type, path.read_bytes())


def as_data_url(media_type: str, data: bytes) -> str:
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{media_type};base64,{encoded}"


def check_payload_audio(name: str, payload: dict[str, Any]) -> dict[str, Any]:
    audio_url = extract_audio_url(payload)
    media_type, encoded = parse_data_url(audio_url)
    decoded = decode_base64_like_vllm(encoded)
    return {
        "case": name,
        "payload": summarize_payload(payload, audio_url),
        "data_url": {
            "media_type": media_type,
            "encoded_chars": len(encoded),
            "decoded_bytes": len(decoded),
            "prefix_hex": decoded[:16].hex(" "),
            "prefix_ascii": ascii_prefix(decoded),
        },
        "stdlib_wave": probe_wave(decoded),
        "soundfile": probe_soundfile(decoded),
        "pyav": probe_pyav(decoded),
    }


def extract_audio_url(payload: dict[str, Any]) -> str:
    content = payload["messages"][0]["content"]
    audio_parts = [part for part in content if part.get("type") == "audio_url"]
    if len(audio_parts) != 1:
        raise ValueError(f"Expected exactly one audio_url part, got {len(audio_parts)}")
    audio_url = audio_parts[0]["audio_url"]["url"]
    if not isinstance(audio_url, str):
        raise TypeError("audio_url.url must be a string")
    return audio_url


def parse_data_url(audio_url: str) -> tuple[str, str]:
    if not audio_url.startswith("data:"):
        raise ValueError("audio URL is not a data URL")
    header, encoded = audio_url.split(",", 1)
    media_type = header.removeprefix("data:").removesuffix(";base64")
    if not header.endswith(";base64"):
        raise ValueError(f"data URL is not base64 encoded: {header}")
    return media_type, encoded


def decode_base64_like_vllm(encoded: str) -> bytes:
    try:
        import pybase64
    except ImportError:
        return base64.b64decode(encoded)
    return pybase64.b64decode(encoded)


def summarize_payload(payload: dict[str, Any], audio_url: str) -> dict[str, Any]:
    content = payload["messages"][0]["content"]
    return {
        "model": payload.get("model"),
        "task": payload.get("task"),
        "message_count": len(payload.get("messages", [])),
        "content_types": [part.get("type") for part in content],
        "has_request_chat_template": "chat_template" in payload,
        "audio_url_prefix": audio_url[:32],
    }


def ascii_prefix(data: bytes) -> str:
    return "".join(chr(byte) if 32 <= byte <= 126 else "." for byte in data[:16])


def probe_wave(data: bytes) -> dict[str, Any]:
    try:
        with wave.open(BytesIO(data), "rb") as wav_file:
            frames = wav_file.getnframes()
            rate = wav_file.getframerate()
            return {
                "ok": True,
                "channels": wav_file.getnchannels(),
                "sample_width": wav_file.getsampwidth(),
                "sample_rate": rate,
                "frames": frames,
                "duration_s": frames / rate,
            }
    except Exception as exc:
        return error_result(exc)


def probe_soundfile(data: bytes) -> dict[str, Any]:
    try:
        import soundfile
    except ImportError as exc:
        return error_result(exc, available=False)
    try:
        with soundfile.SoundFile(BytesIO(data)) as sound_file:
            return {
                "ok": True,
                "version": getattr(soundfile, "__version__", None),
                "libsndfile": getattr(soundfile, "__libsndfile_version__", None),
                "format": sound_file.format,
                "subtype": sound_file.subtype,
                "channels": sound_file.channels,
                "sample_rate": sound_file.samplerate,
                "frames": len(sound_file),
                "duration_s": len(sound_file) / sound_file.samplerate,
            }
    except Exception as exc:
        result = error_result(exc)
        result["code"] = getattr(exc, "code", None)
        return result


def probe_pyav(data: bytes) -> dict[str, Any]:
    try:
        import av
    except ImportError as exc:
        return error_result(exc, available=False)
    try:
        with av.open(BytesIO(data)) as container:
            stream = container.streams.audio[0] if container.streams.audio else None
            if stream is None:
                return {"ok": False, "error_type": "ValueError", "error": "No audio stream"}
            frames = list(container.decode(stream))
            samples = sum(frame.samples for frame in frames)
            return {
                "ok": True,
                "version": getattr(av, "__version__", None),
                "codec": stream.codec_context.name,
                "sample_rate": stream.rate,
                "channels": stream.codec_context.channels,
                "frames": len(frames),
                "samples": samples,
                "duration_s": samples / stream.rate if stream.rate else None,
            }
    except Exception as exc:
        return error_result(exc)


def error_result(exc: Exception, *, available: bool = True) -> dict[str, Any]:
    return {
        "ok": False,
        "available": available,
        "error_type": type(exc).__name__,
        "error": str(exc),
    }


if __name__ == "__main__":
    raise SystemExit(main())
