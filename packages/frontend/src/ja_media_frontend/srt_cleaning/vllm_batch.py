from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console

from ja_media_frontend.srt_cleaning.workspace import run_for_anilist


DEFAULT_IMAGE = "vllm/vllm-openai"
DEFAULT_MODEL = "RedHatAI/gemma-4-26B-A4B-it-NVFP4"
DEFAULT_MAX_MODEL_LEN = "96000"
DEFAULT_MAX_NUM_BATCHED_TOKENS = "16384"
CONTAINER_DATA_ROOT = Path("/data")
console = Console()
error_console = Console(stderr=True)


@dataclass(frozen=True)
class VllmBatchPaths:
    """Host and container paths for a single vLLM batch invocation."""

    data_root: Path
    input_path: Path
    output_path: Path

    @property
    def container_input(self) -> str:
        return container_path(self.input_path, self.data_root)

    @property
    def container_output(self) -> str:
        return container_path(self.output_path, self.data_root)


def register_run_vllm_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = subparsers.add_parser(
        "run-vllm",
        help="Run a generated SRT cleaning batch with local vLLM Docker",
    )
    add_vllm_batch_arguments(parser)


def add_vllm_batch_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--anilist", type=int, help="AniList ID for workspace autodetect")
    parser.add_argument("--workspace-root", help="Override .ja-media-runs root")
    parser.add_argument("--run-id", default="current", help="Workspace run ID")
    parser.add_argument("--input", help="Host batch JSONL path")
    parser.add_argument("--out", help="Host results JSONL path")
    parser.add_argument("--data-root", help="Host directory to mount as /data")
    parser.add_argument("--image", default=DEFAULT_IMAGE, help="Local vLLM Docker image")
    parser.add_argument("--gpus", default="all", help="Docker --gpus value")
    parser.add_argument("--runtime", default="nvidia", help="Docker --runtime value")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="vLLM model")
    parser.add_argument("--max-model-len", default=DEFAULT_MAX_MODEL_LEN)
    parser.add_argument("--max-num-batched-tokens", default=DEFAULT_MAX_NUM_BATCHED_TOKENS)
    parser.add_argument(
        "--hf-cache",
        default="~/.cache/huggingface",
        help="Host Hugging Face cache directory mounted into the container",
    )
    parser.add_argument(
        "--vllm-cache",
        default="vllm-cache",
        help="Docker volume or host path for /root/.cache/vllm",
    )
    parser.add_argument(
        "--env",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Extra container environment variable; repeatable",
    )
    parser.add_argument(
        "--extra-vllm-arg",
        action="append",
        default=[],
        help="Extra argument appended to vllm run-batch; repeatable",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print commands without running")
    parser.add_argument(
        "vllm_args",
        nargs=argparse.REMAINDER,
        help="Arguments after -- are appended to vllm run-batch",
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="srt_clean_vllm_batch",
        description="Run SRT cleaning JSONL through local vLLM Docker",
    )
    add_vllm_batch_arguments(parser)
    run_vllm_batch(parser.parse_args())


def run_vllm_batch(args: argparse.Namespace) -> None:
    """Resolve a workspace batch and run vLLM without hiding Docker output."""

    paths = resolve_paths(args)
    validate_paths(paths)
    env_args = container_env_args(args.env)
    vllm_args = normalize_escape_hatch(args.vllm_args)

    docker_cmd = build_docker_command(args, paths, env_args, vllm_args)
    console.print("[bold]Docker command:[/]")
    console.print(shell_join(docker_cmd))

    if args.dry_run:
        return

    require_command("nvidia-smi")
    require_command("docker")
    run_streaming(["nvidia-smi"])
    run_streaming(["docker", "image", "inspect", args.image])
    run_streaming(build_image_start_check(args, paths, env_args))
    run_streaming(docker_cmd)
    console.print(f"[green]vLLM results:[/] [cyan]{paths.output_path}[/]")


def resolve_paths(args: argparse.Namespace) -> VllmBatchPaths:
    if args.anilist is not None:
        if args.input:
            fail("Use either --anilist or --input, not both.")
        workspace_root = Path(args.workspace_root).expanduser() if args.workspace_root else None
        run = run_for_anilist(args.anilist, workspace_root=workspace_root, run_id=args.run_id)
        input_path = run.run_dir / "batch-00001.jsonl"
        output_path = Path(args.out).expanduser() if args.out else run.results_path
        data_root = Path(args.data_root).expanduser() if args.data_root else run.run_dir
        return VllmBatchPaths(
            data_root=data_root.resolve(),
            input_path=input_path.resolve(),
            output_path=output_path.resolve(),
        )

    if not args.input:
        fail("Provide --anilist or --input.")
    input_path = Path(args.input).expanduser().resolve()
    output_path = (
        Path(args.out).expanduser().resolve()
        if args.out
        else input_path.with_name("results.jsonl")
    )
    data_root = (
        Path(args.data_root).expanduser().resolve()
        if args.data_root
        else input_path.parent
    )
    return VllmBatchPaths(data_root=data_root, input_path=input_path, output_path=output_path)


def validate_paths(paths: VllmBatchPaths) -> None:
    if not paths.input_path.exists():
        fail(f"Input batch JSONL does not exist: {paths.input_path}")
    if not paths.data_root.is_dir():
        fail(f"Data root is not a directory: {paths.data_root}")
    for path, label in ((paths.input_path, "input"), (paths.output_path.parent, "output")):
        try:
            path.relative_to(paths.data_root)
        except ValueError:
            fail(f"{label} path is outside --data-root {paths.data_root}: {path}")


def build_docker_command(
    args: argparse.Namespace,
    paths: VllmBatchPaths,
    env_args: list[str],
    vllm_args: list[str],
) -> list[str]:
    command = docker_base(args, paths, env_args)
    command.extend(
        [
            "run-batch",
            "-i",
            paths.container_input,
            "-o",
            paths.container_output,
            "--model",
            args.model,
            "--max-model-len",
            str(args.max_model_len),
            "--max-num-batched-tokens",
            str(args.max_num_batched_tokens),
        ]
    )
    command.extend(args.extra_vllm_arg)
    command.extend(vllm_args)
    return command


def build_image_start_check(
    args: argparse.Namespace,
    paths: VllmBatchPaths,
    env_args: list[str],
) -> list[str]:
    command = docker_base(args, paths, env_args)
    command.append("--help")
    return command


def docker_base(
    args: argparse.Namespace,
    paths: VllmBatchPaths,
    env_args: list[str],
) -> list[str]:
    hf_cache = Path(args.hf_cache).expanduser().resolve()
    command = [
        "docker",
        "run",
        "--rm",
        "--pull",
        "never",
        "--runtime",
        args.runtime,
        "--gpus",
        args.gpus,
        "-v",
        f"{hf_cache}:/root/.cache/huggingface",
        "-v",
        f"{args.vllm_cache}:/root/.cache/vllm",
        "-v",
        f"{paths.data_root}:/data",
        "-e",
        "VLLM_SKIP_MODEL_NAME_VALIDATION=1",
        "-e",
        "HF_HOME=/root/.cache/huggingface",
    ]
    command.extend(env_args)
    command.extend(["--entrypoint", "vllm", args.image])
    return command


def container_env_args(items: list[str]) -> list[str]:
    args: list[str] = []
    for item in items:
        if "=" not in item or item.startswith("="):
            fail(f"--env must be KEY=VALUE, got: {item!r}")
        args.extend(["-e", item])
    return args


def normalize_escape_hatch(items: list[str]) -> list[str]:
    if items and items[0] == "--":
        return items[1:]
    return items


def container_path(path: Path, data_root: Path) -> str:
    return str(CONTAINER_DATA_ROOT / path.relative_to(data_root))


def require_command(name: str) -> None:
    if shutil.which(name) is None:
        fail(f"Required command not found on PATH: {name}")


def run_streaming(command: list[str]) -> None:
    console.print(f"[dim]$ {shell_join(command)}[/]")
    try:
        completed = subprocess.run(command, check=False)
    except FileNotFoundError:
        fail(f"Required command not found on PATH: {command[0]}")
    if completed.returncode != 0:
        sys.exit(completed.returncode)


def shell_join(command: list[str]) -> str:
    return shlex.join(os.fspath(part) for part in command)


def fail(message: str) -> None:
    error_console.print(f"[bold red]Error:[/] {message}")
    sys.exit(2)


if __name__ == "__main__":
    main()
