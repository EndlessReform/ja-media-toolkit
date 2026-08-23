"""Private CLI for running prepared forced-alignment retiming cases."""

from __future__ import annotations

import argparse
from pathlib import Path

from ja_media_inference.forced_alignment.case_runner import (
    align_full_case,
    compare_case_windows,
)
from ja_media_inference.forced_alignment.control_runner import run_absent_text_controls
from ja_media_inference.forced_alignment.stability_runner import run_stability_case
from ja_media_inference.forced_alignment.stress_runner import run_concurrency_sweep


def main() -> None:
    parser = argparse.ArgumentParser(prog="qwen3-retime-case")
    parser.add_argument(
        "command", choices=("compare", "full", "controls", "stability", "stress")
    )
    parser.add_argument("case_manifest", type=Path)
    parser.add_argument("--base-url", required=True)
    parser.add_argument(
        "--window-sizes",
        default="30,60,180",
        help="Comma-separated comparison windows in seconds.",
    )
    parser.add_argument(
        "--vad-plan",
        type=Path,
        help="JSON output from ja-media vad-local --split-every-minutes.",
    )
    parser.add_argument(
        "--boundary-radius-s",
        type=float,
        default=30.0,
        help="Audio on each side of a VAD cut; 30s keeps probes 60s long.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional comparison result path; preserves earlier comparison arms.",
    )
    parser.add_argument(
        "--text-base",
        choices=("cleaned", "mechanical"),
        default="cleaned",
        help="Use final cleaned text or deterministic mechanical text.",
    )
    parser.add_argument(
        "--sample-count",
        type=int,
        default=6,
        help="Lexical dialogue cues to use for the stability harness.",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=32,
        help="Maximum simultaneous full-alignment or stability requests.",
    )
    parser.add_argument(
        "--concurrency-levels",
        default="1,2,4,8,16,32",
        help="Comma-separated concurrency sweep levels.",
    )
    parser.add_argument(
        "--stress-audio-pattern",
        choices=("unique", "repeated"),
        default="unique",
        help="Use production-like unique crops or a deliberately warm repeated crop.",
    )
    args = parser.parse_args()
    manifest = args.case_manifest.expanduser().resolve()
    text_field = {
        "cleaned": "alignment_text",
        "mechanical": "mechanical_text",
    }[args.text_base]
    if args.command == "compare":
        sizes = tuple(float(value) for value in args.window_sizes.split(","))
        result = compare_case_windows(
            manifest,
            base_url=args.base_url,
            window_sizes_s=sizes,
            output_path=args.output,
            text_field=text_field,
        )
        print(f"window_comparison={result}")
    elif args.command == "full":
        if args.vad_plan is None:
            parser.error("full requires --vad-plan")
        result = align_full_case(
            manifest,
            base_url=args.base_url,
            vad_plan_path=args.vad_plan.expanduser().resolve(),
            boundary_radius_s=args.boundary_radius_s,
            text_field=text_field,
            concurrency=args.concurrency,
        )
        print(f"full_alignment={result}")
    elif args.command == "controls":
        result = run_absent_text_controls(manifest, base_url=args.base_url)
        print(f"confidence_controls={result}")
    elif args.command == "stability":
        sizes = tuple(float(value) for value in args.window_sizes.split(","))
        result = run_stability_case(
            manifest,
            base_url=args.base_url,
            sample_count=args.sample_count,
            window_sizes_s=sizes,
            text_field=text_field,
            concurrency=args.concurrency,
        )
        print(f"stability={result}")
    else:
        levels = tuple(int(value) for value in args.concurrency_levels.split(","))
        result = run_concurrency_sweep(
            manifest,
            base_url=args.base_url,
            levels=levels,
            text_field=text_field,
            audio_pattern=args.stress_audio_pattern,
        )
        print(f"stress={result}")


if __name__ == "__main__":
    main()
