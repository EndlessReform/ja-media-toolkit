"""Deterministic layered environment loading for source-tree CLI launches."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values


def load_cli_environment(start: Path | None = None) -> tuple[Path, ...]:
    """Merge repo-local ``.env`` files from root to the invocation directory.

    The nearest Git boundary limits discovery. Values in more specific files
    replace values from their ancestors, while variables already exported by
    the launching process remain authoritative. Outside a Git worktree, only
    the invocation directory is considered.
    """

    invocation_dir = (start or Path.cwd()).resolve()
    repository_root = _repository_root(invocation_dir)
    directories = _directories_from_root(repository_root, invocation_dir)
    env_files = tuple(
        directory / ".env"
        for directory in directories
        if (directory / ".env").is_file()
    )
    merged: dict[str, str] = {}
    for env_file in env_files:
        merged.update(
            {
                key: value
                for key, value in dotenv_values(env_file).items()
                if value is not None
            }
        )
    for key, value in merged.items():
        os.environ.setdefault(key, value)
    return env_files


def _repository_root(start: Path) -> Path:
    for directory in (start, *start.parents):
        if (directory / ".git").exists():
            return directory
    return start


def _directories_from_root(root: Path, leaf: Path) -> tuple[Path, ...]:
    relative = leaf.relative_to(root)
    directories = [root]
    current = root
    for part in relative.parts:
        current /= part
        directories.append(current)
    return tuple(directories)
