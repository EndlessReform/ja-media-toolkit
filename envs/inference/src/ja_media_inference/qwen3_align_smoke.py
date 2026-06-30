from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

from ja_media_inference.forced_alignment import (
    Qwen3VllmForcedAligner,
    groups_from_lines,
    merge_token_alignments_by_group,
    segment_group_with_nagisa,
)


DEFAULT_MODEL = "Qwen/Qwen3-ForcedAligner-0.6B"
DEFAULT_FIXTURE = Path("../../examples/forced-alignment/qwen3-tts")


def main() -> int:
    args = parse_args()
    fixture_dir = args.fixture_dir.resolve()
    audio_path = fixture_dir / "openai-tts-ground-truth.wav"
    source_path = fixture_dir / "source.txt"
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    text = source_path.read_text(encoding="utf-8")
    groups = groups_from_lines(
        text,
        group_prefix="qwen3-tts-line",
        metadata={"fixture": str(fixture_dir)},
    )
    tokens = [
        token
        for group in groups
        for token in segment_group_with_nagisa(group)
    ]
    if not tokens:
        raise RuntimeError("nagisa produced no alignment tokens")

    aligner = Qwen3VllmForcedAligner(
        base_url=args.base_url,
        model=args.model,
        prompt_layout=args.prompt_layout,
        trust_request_chat_template=args.trust_request_chat_template,
    )
    token_alignments = aligner.align_tokens(audio_path=audio_path, tokens=tokens)
    group_alignments = merge_token_alignments_by_group(groups, token_alignments)
    duration_s = media_duration_s(audio_path)
    diagnostics = diagnose(token_alignments, duration_s)

    report = {
        "model": args.model,
        "base_url": args.base_url,
        "audio_path": str(audio_path),
        "source_path": str(source_path),
        "audio_duration_s": duration_s,
        "prompt_layout": args.prompt_layout,
        "groups": [asdict(group) for group in groups],
        "tokens": [asdict(token) for token in tokens],
        "token_alignments": [
            {
                "token_id": item.token.id,
                "text": item.token.text,
                "group_id": item.token.group_id,
                "group_index": item.token.group_index,
                "start_s": item.start_s,
                "end_s": item.end_s,
            }
            for item in token_alignments
        ],
        "group_alignments": {
            group_id: asdict(alignment)
            for group_id, alignment in group_alignments.items()
        },
        "diagnostics": diagnostics,
    }
    report_path = output_dir / "qwen3-align-smoke-report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"Audio: {audio_path} ({duration_s:.2f}s)")
    print(f"Source: {source_path}")
    print(f"Groups: {len(groups)}")
    print(f"Tokens: {len(tokens)}")
    print(f"Report: {report_path}")
    print("Group alignments:")
    for group in groups:
        alignment = group_alignments[group.id]
        print(
            f"{alignment.start_s:7.3f}-{alignment.end_s:7.3f}s "
            f"{group.id} {group.text}"
        )
    print("Token alignments:")
    for item in token_alignments:
        print(f"{item.start_s:7.3f}-{item.end_s:7.3f}s {item.token.text}")
    if diagnostics:
        print("Diagnostics:")
        for item in diagnostics:
            print(f"- {item['severity']} {item['code']}: {item['message']}")
    return 0 if not any(item["severity"] == "error" for item in diagnostics) else 2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://melchior-1:8000")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--fixture-dir",
        type=Path,
        default=DEFAULT_FIXTURE,
        help="Directory containing source.txt and openai-tts-ground-truth.wav.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("../../scripts/exploration/out/qwen3-vllm-tts-smoke"),
    )
    parser.add_argument(
        "--prompt-layout",
        choices=("after-token", "wrap-token"),
        default="after-token",
    )
    parser.add_argument(
        "--trust-request-chat-template",
        action="store_true",
        help="Include the upstream raw-content chat template in each request.",
    )
    return parser.parse_args()


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


def diagnose(token_alignments: list, audio_duration_s: float) -> list[dict[str, object]]:
    diagnostics: list[dict[str, object]] = []
    previous_start = -1.0
    for item in token_alignments:
        if item.end_s < item.start_s:
            diagnostics.append(
                {
                    "severity": "error",
                    "code": "non_monotonic_token",
                    "message": f"{item.token.id} ends before it starts.",
                }
            )
        if item.start_s < previous_start - 0.5:
            diagnostics.append(
                {
                    "severity": "warning",
                    "code": "token_time_went_backward",
                    "message": f"{item.token.id} starts before an earlier token.",
                }
            )
        if item.end_s > audio_duration_s + 2.0:
            diagnostics.append(
                {
                    "severity": "error",
                    "code": "token_after_audio",
                    "message": f"{item.token.id} ends after the audio duration.",
                }
            )
        previous_start = max(previous_start, item.start_s)
    return diagnostics


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
