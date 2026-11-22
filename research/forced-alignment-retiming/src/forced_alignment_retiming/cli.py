"""Private command line for the first forced-alignment retiming slice."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from forced_alignment_retiming.canonical import open_dev_inputs, resolve_case
from forced_alignment_retiming.cases import load_case
from forced_alignment_retiming.cleaned_input import prepare_cleaned_input
from forced_alignment_retiming.media import cache_canonical_media
from forced_alignment_retiming.pull import pull_candidates
from forced_alignment_retiming.ranking import pair_case
from forced_alignment_retiming.slice import discover_cleaned_slice


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = PROJECT_ROOT.parents[1]


def main() -> None:
    """Run one bounded research operation without adding a public command."""

    parser = argparse.ArgumentParser(prog="retiming-research")
    commands = parser.add_subparsers(dest="command", required=True)
    pair = commands.add_parser(
        "pair", help="select one Kitsunekko source for one canonical audio case"
    )
    pair.add_argument("case")
    pair.add_argument("--cases", type=Path, default=PROJECT_ROOT / "cases.toml")
    pair.add_argument("--output-root", type=Path, default=PROJECT_ROOT / "output")
    pair.add_argument(
        "--data-config",
        type=Path,
        default=REPO_ROOT / "packages" / "data" / "config.dev.toml",
    )
    prepare = commands.add_parser(
        "prepare", help="pin cleaned cues and canonical audio for one case"
    )
    prepare.add_argument("case")
    prepare.add_argument("--cases", type=Path, default=PROJECT_ROOT / "cases.toml")
    prepare.add_argument("--output-root", type=Path, default=PROJECT_ROOT / "output")
    prepare.add_argument(
        "--data-config",
        type=Path,
        default=REPO_ROOT / "packages" / "data" / "config.dev.toml",
    )
    prepare_slice = commands.add_parser(
        "prepare-slice", help="prepare every unique subtitle in one cleaning run"
    )
    prepare_slice.add_argument("reconstruct", type=Path)
    prepare_slice.add_argument("--episode", type=int, required=True)
    prepare_slice.add_argument("--output-root", type=Path, default=PROJECT_ROOT / "output")
    prepare_slice.add_argument(
        "--data-config",
        type=Path,
        default=REPO_ROOT / "packages" / "data" / "config.dev.toml",
    )
    args = parser.parse_args()

    if args.command == "prepare-slice":
        _prepare_slice(args, parser)
        return

    selected = load_case(args.cases.expanduser().resolve(), args.case)
    data_config = args.data_config.expanduser().resolve()
    if not data_config.is_file():
        parser.error(f"data config not found: {data_config}")
    output_root = args.output_root.expanduser().resolve()
    with open_dev_inputs(data_config) as (connection, bronze):
        canonical = resolve_case(connection, selected)
        if args.command == "prepare":
            _prepare(selected, canonical, bronze, output_root)
            return
        candidate_manifest = pull_candidates(
            selected, canonical, output_root
        )
        audio, anchors = cache_canonical_media(
            canonical, candidate_manifest.parent, bronze
        )
    ranking = pair_case(
        selected, canonical, audio, anchors, candidate_manifest
    )
    print(f"candidate_manifest={candidate_manifest}")
    print(f"pairing={ranking}")


def _prepare(selected, canonical, bronze, output_root: Path) -> None:
    """Materialize the exact text/audio pair used by later experiments."""

    if not selected.cleaning_reconstruct:
        raise ValueError(f"case {selected.name!r} has no cleaning reconstruction")
    case_root = output_root / selected.name
    case_root.mkdir(parents=True, exist_ok=True)
    cleaned = prepare_cleaned_input(
        selected, REPO_ROOT / selected.cleaning_reconstruct, case_root
    )
    audio, anchors = cache_canonical_media(canonical, case_root, bronze)
    manifest = {
        "schema_name": "ja-media.forced-alignment.case",
        "schema_version": "1.0.0",
        "case": selected.name,
        "canonical": canonical,
        "cleaned_subtitle": cleaned,
        "audio": audio,
        "embedded_subtitles": anchors,
    }
    path = case_root / "case.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(f"case_manifest={path}")


def _prepare_slice(args, parser: argparse.ArgumentParser) -> None:  # type: ignore[no-untyped-def]
    """Prepare a cleaning run while sharing one data connection."""

    reconstruct = args.reconstruct.expanduser().resolve()
    data_config = args.data_config.expanduser().resolve()
    if not reconstruct.is_dir():
        parser.error(f"reconstruction not found: {reconstruct}")
    if not data_config.is_file():
        parser.error(f"data config not found: {data_config}")
    output_root = args.output_root.expanduser().resolve()
    selected = discover_cleaned_slice(reconstruct, episode=args.episode)
    manifests = []
    with open_dev_inputs(data_config) as (connection, bronze):
        for item in selected:
            canonical = resolve_case(connection, item.case)
            _prepare(item.case, canonical, bronze, output_root)
            manifests.append(
                {
                    "case": item.case.name,
                    "case_manifest": str(output_root / item.case.name / "case.json"),
                    "catalog_subtitle_ids": list(item.catalog_subtitle_ids),
                }
            )
    slice_manifest = output_root / "slice.json"
    slice_manifest.write_text(
        json.dumps(
            {
                "schema_name": "ja-media.forced-alignment.prepared-slice",
                "schema_version": "1.0.0",
                "episode": args.episode,
                "source_count": len(manifests),
                "cases": manifests,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"slice_manifest={slice_manifest}")


if __name__ == "__main__":
    main()
