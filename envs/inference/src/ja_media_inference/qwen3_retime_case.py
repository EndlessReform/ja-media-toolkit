"""Private CLI for running prepared forced-alignment retiming cases."""

from __future__ import annotations

import argparse
from pathlib import Path

from ja_media_inference.forced_alignment.case_runner import (
    align_full_case,
    compare_case_windows,
)
from ja_media_inference.forced_alignment.control_runner import run_absent_text_controls


def main() -> None:
    parser = argparse.ArgumentParser(prog="qwen3-retime-case")
    parser.add_argument("command", choices=("compare", "full", "controls"))
    parser.add_argument("case_manifest", type=Path)
    parser.add_argument("--base-url", required=True)
    parser.add_argument(
        "--window-sizes", default="30,60,180",
        help="Comma-separated comparison windows in seconds.",
    )
    parser.add_argument(
        "--full-window-size", type=float, default=180.0,
        help="Window size in seconds for the full-episode run.",
    )
    parser.add_argument(
        "--output", type=Path,
        help="Optional comparison result path; preserves earlier comparison arms.",
    )
    parser.add_argument(
        "--text-base", choices=("cleaned", "mechanical"), default="cleaned",
        help="Use final cleaned text or deterministic mechanical text.",
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
        result = align_full_case(
            manifest,
            base_url=args.base_url,
            window_s=args.full_window_size,
            text_field=text_field,
        )
        print(f"full_alignment={result}")
    else:
        result = run_absent_text_controls(manifest, base_url=args.base_url)
        print(f"confidence_controls={result}")


if __name__ == "__main__":
    main()
