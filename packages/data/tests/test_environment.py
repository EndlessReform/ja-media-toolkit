"""Tests for explicit monorepo ``.env`` layering."""

import os
from pathlib import Path

from ja_media_data.environment import load_cli_environment


def test_environment_merges_root_to_cwd_with_specific_values_winning(
    tmp_path: Path, monkeypatch
) -> None:
    repository = tmp_path / "repo"
    package = repository / "packages" / "data"
    package.mkdir(parents=True)
    (repository / ".git").mkdir()
    (repository / ".env").write_text(
        "OPERATOR_ROOT_ONLY=root\nOPERATOR_SHARED=root\n",
        encoding="utf-8",
    )
    (package / ".env").write_text(
        "OPERATOR_PACKAGE_ONLY=package\nOPERATOR_SHARED=package\n",
        encoding="utf-8",
    )
    for name in ("OPERATOR_ROOT_ONLY", "OPERATOR_PACKAGE_ONLY", "OPERATOR_SHARED"):
        monkeypatch.delenv(name, raising=False)

    loaded = load_cli_environment(package)

    assert loaded == (repository / ".env", package / ".env")
    assert os.environ["OPERATOR_ROOT_ONLY"] == "root"
    assert os.environ["OPERATOR_PACKAGE_ONLY"] == "package"
    assert os.environ["OPERATOR_SHARED"] == "package"


def test_exported_environment_wins_over_all_files(
    tmp_path: Path, monkeypatch
) -> None:
    repository = tmp_path / "repo"
    package = repository / "package"
    package.mkdir(parents=True)
    (repository / ".git").mkdir()
    (repository / ".env").write_text("OPERATOR_SHARED=root\n", encoding="utf-8")
    (package / ".env").write_text("OPERATOR_SHARED=package\n", encoding="utf-8")
    monkeypatch.setenv("OPERATOR_SHARED", "exported")

    load_cli_environment(package)

    assert os.environ["OPERATOR_SHARED"] == "exported"


def test_discovery_does_not_cross_repository_boundary(
    tmp_path: Path, monkeypatch
) -> None:
    repository = tmp_path / "repo"
    package = repository / "package"
    package.mkdir(parents=True)
    (repository / ".git").mkdir()
    (tmp_path / ".env").write_text("OPERATOR_OUTSIDE=wrong\n", encoding="utf-8")
    monkeypatch.delenv("OPERATOR_OUTSIDE", raising=False)

    loaded = load_cli_environment(package)

    assert loaded == ()
    assert "OPERATOR_OUTSIDE" not in os.environ
