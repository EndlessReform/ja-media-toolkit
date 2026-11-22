from __future__ import annotations

from pathlib import Path
import pytest

from ja_media_frontend.srt_cleaning.cli import build_parser
from ja_media_frontend.srt_cleaning.settings import SrtCleanSettings


def test_settings_load_toml_and_env_precedence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        """
[srt_clean]
workspace_root = "/tmp/custom-runs"
default_backend = "deepseek"

[srt_clean.generate]
model = "toml-model"
window_size = 14

[srt_clean.provider]
concurrency = 6
""".strip(),
        encoding="utf-8",
    )

    settings = SrtCleanSettings.load(config_file)
    assert settings.workspace_root == Path("/tmp/custom-runs")
    assert settings.default_backend == "deepseek"
    assert settings.generate.model == "toml-model"
    assert settings.generate.window_size == 14
    assert settings.provider.concurrency == 6

    monkeypatch.setenv("JA_MEDIA_SRT_CLEAN_DEFAULT_BACKEND", "openai")
    monkeypatch.setenv("JA_MEDIA_SRT_CLEAN_GENERATE__WINDOW_SIZE", "20")
    overridden = SrtCleanSettings.load(config_file)
    assert overridden.default_backend == "openai"
    assert overridden.generate.window_size == 20
    assert overridden.generate.model == "toml-model"


def test_cli_uses_settings_defaults_and_allows_flag_override(tmp_path: Path) -> None:
    config_file = tmp_path / "srt-clean.toml"
    config_file.write_text(
        """
default_backend = "openai"

[generate]
model = "toml-gen-model"
window_size = 12
""".strip(),
        encoding="utf-8",
    )
    settings = SrtCleanSettings.load(config_file)
    parser = build_parser(settings)

    gen_args = parser.parse_args(["generate", "--anilist", "184591", "--dry-run"])
    assert gen_args.command == "generate"
    assert gen_args.dry_run is True
    assert gen_args.model == "toml-gen-model"
    assert gen_args.window_size == 12

    gen_override = parser.parse_args(
        ["generate", "--anilist", "184591", "--window-size", "8"]
    )
    assert gen_override.window_size == 8

    run_args = parser.parse_args(["run", "--anilist", "184591"])
    assert run_args.command == "run"
    assert run_args.backend == "openai"
