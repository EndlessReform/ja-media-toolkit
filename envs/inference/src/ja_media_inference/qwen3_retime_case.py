"""Private CLI for running prepared forced-alignment retiming cases."""

from __future__ import annotations

import argparse
from pathlib import Path

from ja_media_core.config import load_config
from ja_media_inference.forced_alignment.edge_campaign import run_edge_campaign
from ja_media_inference.forced_alignment.edge_inventory import (
    build_candidate_inventory,
    load_slice_cases,
    write_inventory,
)
from ja_media_inference.forced_alignment.edge_targets import (
    build_edge_targets,
    write_edge_targets,
)
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
        "command",
        choices=("compare", "full", "controls", "stability", "stress", "edge"),
    )
    parser.add_argument("case_manifest", type=Path)
    parser.add_argument("--base-url")
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
        help="Optional comparison result path; preserves earlier test versions.",
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
    parser.add_argument(
        "--reviewed-targets",
        type=Path,
        help="Reviewed ordinary-dialogue target JSON used by the edge campaign.",
    )
    parser.add_argument(
        "--edge-stage",
        choices=("prepare", "run", "all"),
        default="all",
        help="Prepare inventory/targets, run requests, or do both.",
    )
    parser.add_argument(
        "--edge-clearance-s",
        type=float,
        default=2.0,
        help="Complete source-cue clearance from beginning/end crop edges.",
    )
    args = parser.parse_args()
    manifest = args.case_manifest.expanduser().resolve()
    if args.command == "edge":
        _run_edge(args, parser, manifest)
        return
    base_url = _base_url(args.base_url, parser)
    text_field = {
        "cleaned": "alignment_text",
        "mechanical": "mechanical_text",
    }[args.text_base]
    if args.command == "compare":
        sizes = tuple(float(value) for value in args.window_sizes.split(","))
        result = compare_case_windows(
            manifest,
            base_url=base_url,
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
            base_url=base_url,
            vad_plan_path=args.vad_plan.expanduser().resolve(),
            boundary_radius_s=args.boundary_radius_s,
            text_field=text_field,
            concurrency=args.concurrency,
        )
        print(f"full_alignment={result}")
    elif args.command == "controls":
        result = run_absent_text_controls(manifest, base_url=base_url)
        print(f"confidence_controls={result}")
    elif args.command == "stability":
        sizes = tuple(float(value) for value in args.window_sizes.split(","))
        result = run_stability_case(
            manifest,
            base_url=base_url,
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
            base_url=base_url,
            levels=levels,
            text_field=text_field,
            audio_pattern=args.stress_audio_pattern,
        )
        print(f"stress={result}")


def _run_edge(
    args: argparse.Namespace, parser: argparse.ArgumentParser, slice_path: Path
) -> None:
    if args.reviewed_targets is None:
        parser.error("edge requires --reviewed-targets")
    reviewed = args.reviewed_targets.expanduser().resolve()
    output_dir = slice_path.parent / "edge-experiment"
    targets_path = output_dir / "targets.json"
    if args.edge_stage in {"prepare", "all"}:
        cases = load_slice_cases(slice_path)
        inventory = build_candidate_inventory(cases)
        inventory_paths = write_inventory(inventory, output_dir)
        targets = build_edge_targets(
            slice_path,
            reviewed,
            edge_clearance_s=args.edge_clearance_s,
        )
        targets_path = write_edge_targets(targets, output_dir)
        print(f"edge_inventory={inventory_paths[0]}")
        print(f"edge_summary={inventory_paths[2]}")
        print(f"edge_targets={targets_path}")
    if args.edge_stage in {"run", "all"}:
        if not targets_path.is_file():
            parser.error(f"edge target manifest not found: {targets_path}")
        result = run_edge_campaign(
            slice_path,
            targets_path,
            base_url=_base_url(args.base_url, parser),
            concurrency=args.concurrency,
        )
        print(f"edge_results={result}")


def _base_url(value: str | None, parser: argparse.ArgumentParser) -> str:
    if value:
        return value
    try:
        backend = load_config(required=True).forced_alignment.get_backend_config()
    except (FileNotFoundError, KeyError, ValueError) as exc:
        parser.error(f"forced-alignment endpoint is not configured: {exc}")
    raw = backend.model_dump()
    for key in ("base_url", "adapter_base_url", "vllm_base_url"):
        configured = raw.get(key)
        if isinstance(configured, str) and configured:
            return configured
    parser.error("configured forced-alignment backend has no adapter URL")
    raise AssertionError("argparse.error did not exit")


if __name__ == "__main__":
    main()
