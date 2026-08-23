"""The pre-cleaning funnel chooses a Japanese source against a usable anchor."""

from __future__ import annotations

import json
from pathlib import Path

from forced_alignment_retiming.cases import CanonicalCase
from forced_alignment_retiming.media import _probe_duration
from forced_alignment_retiming.ranking import pair_case


def test_pair_case_rejects_foreign_candidate_and_selects_japanese(tmp_path: Path) -> None:
    case_root = tmp_path / "happy"
    candidates = case_root / "candidates"
    anchors = case_root / "anchors"
    candidates.mkdir(parents=True)
    anchors.mkdir()
    (anchors / "anchor.srt").write_text(_srt("これは日本語の字幕です"))
    (candidates / "ja.srt").write_text(_srt("これは日本語の台詞です"))
    (candidates / "en.srt").write_text(_srt("This subtitle line is English only"))
    manifest = case_root / "candidate-pull.json"
    manifest.write_text(
        json.dumps(
            {
                "candidates": [
                    _candidate("english", "candidates/en.srt", "en"),
                    _candidate("japanese", "candidates/ja.srt", "ja"),
                ]
            }
        )
    )

    result = pair_case(
        CanonicalCase("happy", "anilist", "7647", "9"),
        {"canonical_id": "canonical-1"},
        {"duration_s": 130.0, "relative_path": "audio/audio.ac3"},
        [
            {
                "subtitle_input_id": "anchor-1",
                "relative_path": "anchors/anchor.srt",
                "codec": "srt",
            }
        ],
        manifest,
    )

    payload = json.loads(result.read_text())
    assert payload["status"] == "paired"
    assert payload["selected_subtitle_id"] == "japanese"
    assert payload["candidates"][1]["gate_reasons"] == ["non_japanese"]
    assert (case_root / "source.srt").is_file()


def test_raw_ac3_duration_comes_from_header_bitrate(tmp_path: Path) -> None:
    path = tmp_path / "audio.ac3"
    path.write_bytes(b"\x0b\x77\x00\x00\x14" + bytes(7680 - 5))

    assert _probe_duration(path, "ac3") == 0.32


def _candidate(subtitle_id: str, path: str, language_hint: str) -> dict[str, object]:
    return {
        "subtitle_id": subtitle_id,
        "status": "ok",
        "mapped_episode": 9,
        "relative_path": path,
        "metadata": {
            "episode_local": 9,
            "extension": "srt",
            "language_hint": language_hint,
        },
    }


def _srt(text: str) -> str:
    blocks = []
    for index in range(1, 121):
        start = index - 1
        blocks.append(
            f"{index}\n00:{start // 60:02d}:{start % 60:02d},000 --> "
            f"00:{start // 60:02d}:{start % 60:02d},900\n{text}\n"
        )
    return "\n".join(blocks)
