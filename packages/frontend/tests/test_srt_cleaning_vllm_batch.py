from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from ja_media_frontend.srt_cleaning import vllm_batch
from ja_media_frontend.srt_cleaning.cli import build_parser


def args(**overrides: object) -> argparse.Namespace:
    defaults: dict[str, object] = {
        "anilist": None,
        "workspace_root": None,
        "run_id": "current",
        "input": None,
        "out": None,
        "data_root": None,
        "image": vllm_batch.DEFAULT_IMAGE,
        "gpus": "all",
        "runtime": "nvidia",
        "model": vllm_batch.DEFAULT_MODEL,
        "max_model_len": vllm_batch.DEFAULT_MAX_MODEL_LEN,
        "max_num_batched_tokens": vllm_batch.DEFAULT_MAX_NUM_BATCHED_TOKENS,
        "hf_cache": "~/.cache/huggingface",
        "vllm_cache": "vllm-cache",
        "env": [],
        "extra_vllm_arg": [],
        "dry_run": False,
        "vllm_args": [],
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def test_run_vllm_parser_accepts_escape_hatch_args() -> None:
    parser = build_parser()

    parsed = parser.parse_args(
        [
            "run-vllm",
            "--anilist",
            "184591",
            "--model",
            "custom/model",
            "--",
            "--tensor-parallel-size",
            "2",
        ]
    )

    assert parsed.command == "run-vllm"
    assert parsed.anilist == 184591
    assert parsed.model == "custom/model"
    assert vllm_batch.normalize_escape_hatch(parsed.vllm_args) == [
        "--tensor-parallel-size",
        "2",
    ]


def test_console_script_parser_accepts_input_mode(tmp_path: Path) -> None:
    batch = tmp_path / "batch-00001.jsonl"
    batch.write_text("{}", encoding="utf-8")

    parsed = args(input=str(batch))
    paths = vllm_batch.resolve_paths(parsed)

    assert paths.data_root == tmp_path
    assert paths.input_path == batch
    assert paths.output_path == tmp_path / "results.jsonl"


def test_anilist_mode_defaults_to_workspace_paths(tmp_path: Path) -> None:
    parsed = args(anilist=184591, workspace_root=str(tmp_path))

    paths = vllm_batch.resolve_paths(parsed)

    assert paths.data_root == tmp_path / "srt-clean" / "anilist-184591" / "current"
    assert paths.input_path == paths.data_root / "batch-00001.jsonl"
    assert paths.output_path == paths.data_root / "results.jsonl"


def test_validate_paths_refuses_output_outside_data_root(tmp_path: Path) -> None:
    data_root = tmp_path / "run"
    data_root.mkdir()
    batch = data_root / "batch-00001.jsonl"
    batch.write_text("{}", encoding="utf-8")
    outside = tmp_path / "outside" / "results.jsonl"
    outside.parent.mkdir()

    with pytest.raises(SystemExit) as exc:
        vllm_batch.validate_paths(
            vllm_batch.VllmBatchPaths(
                data_root=data_root,
                input_path=batch,
                output_path=outside,
            )
        )

    assert exc.value.code == 2


def test_docker_command_uses_sane_defaults_and_vllm_escape_hatch(tmp_path: Path) -> None:
    batch = tmp_path / "batch-00001.jsonl"
    batch.write_text("{}", encoding="utf-8")
    parsed = args(
        input=str(batch),
        hf_cache=str(tmp_path / "hf"),
        env=["FOO=bar"],
        extra_vllm_arg=["--disable-log-requests"],
    )
    paths = vllm_batch.resolve_paths(parsed)

    command = vllm_batch.build_docker_command(
        parsed,
        paths,
        vllm_batch.container_env_args(parsed.env),
        ["--tensor-parallel-size", "2"],
    )

    assert command[:6] == ["docker", "run", "--rm", "--pull", "never", "--runtime"]
    assert "--gpus" in command
    assert "all" in command
    assert "--entrypoint" in command
    assert command[command.index("--entrypoint") + 1] == "vllm"
    assert command[command.index("-i") + 1] == "/data/batch-00001.jsonl"
    assert command[command.index("-o") + 1] == "/data/results.jsonl"
    assert command[command.index("--model") + 1] == vllm_batch.DEFAULT_MODEL
    assert "--max-model-len" in command
    assert vllm_batch.DEFAULT_MAX_MODEL_LEN in command
    assert "--max-num-batched-tokens" in command
    assert vllm_batch.DEFAULT_MAX_NUM_BATCHED_TOKENS in command
    assert ["-e", "FOO=bar"] == command[command.index("FOO=bar") - 1 : command.index("FOO=bar") + 1]
    assert command[-3:] == ["--disable-log-requests", "--tensor-parallel-size", "2"]
