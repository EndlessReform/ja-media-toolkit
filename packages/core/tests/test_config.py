from __future__ import annotations

from pathlib import Path

import pytest

from ja_media_core.config import load_config


def test_global_config_loads_vad_defaults(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "[vad]\n"
        "threshold = 0.25\n"
        "min_speech_s = 0.10\n"
        "min_silence_s = 0.08\n"
        "speech_pad_s = 0.12\n"
        "merge_gap_s = 0.05\n"
        "channel = 0\n",
        encoding="utf-8",
    )

    options = load_config(config_path).vad.to_options()

    assert options.threshold == pytest.approx(0.25)
    assert options.min_speech_s == pytest.approx(0.10)
    assert options.min_silence_s == pytest.approx(0.08)
    assert options.speech_pad_s == pytest.approx(0.12)
    assert options.merge_gap_s == pytest.approx(0.05)
    assert options.channel == 0


def test_vad_config_overrides_ignore_none_values(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "[vad]\n"
        "threshold = 0.40\n"
        "min_speech_s = 0.10\n",
        encoding="utf-8",
    )

    options = load_config(config_path).vad.to_options(
        threshold=None,
        min_speech_s=0.20,
    )

    assert options.threshold == pytest.approx(0.40)
    assert options.min_speech_s == pytest.approx(0.20)
