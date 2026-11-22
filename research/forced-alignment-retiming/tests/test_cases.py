"""Focused checks for explicit retiming case selection."""

from pathlib import Path

import pytest

from forced_alignment_retiming.cases import load_case


def test_loads_canonical_case(tmp_path: Path) -> None:
    path = tmp_path / "cases.toml"
    path.write_text(
        """schema_version = 1
[[case]]
name = "happy"
namespace = "anilist"
series_id = "7647"
episode = "9"
prior_identity_score = 0.767
"""
    )

    selected = load_case(path, "happy")

    assert selected.series_id == "7647"
    assert selected.episode == "9"
    assert selected.prior_identity_score == pytest.approx(0.767)


def test_rejects_non_anilist_case(tmp_path: Path) -> None:
    path = tmp_path / "cases.toml"
    path.write_text(
        """schema_version = 1
[[case]]
name = "wrong"
namespace = "tvdb"
series_id = "123"
episode = "1"
"""
    )

    with pytest.raises(ValueError, match="namespace"):
        load_case(path, "wrong")
