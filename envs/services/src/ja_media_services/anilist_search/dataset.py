from __future__ import annotations

import filecmp
import logging
import os
import shutil
from pathlib import Path

import kagglehub

DATASET_HANDLE = "calebmwelsh/anilist-anime-dataset"
CSV_NAME = "anilist_anime_data_complete.csv"
REVISION_NAME = "anilist_dataset_revision.txt"

logger = logging.getLogger("ja_media_services.anilist_search.dataset")


def _download_current_dataset() -> tuple[Path, str]:
    """Resolve the latest Kaggle revision into KaggleHub's versioned cache."""
    cached_path = Path(
        kagglehub.dataset_download(DATASET_HANDLE, path=CSV_NAME)
    )
    for parent in cached_path.parents:
        if parent.parent.name == "versions":
            return cached_path, parent.name
    raise RuntimeError(
        f"KaggleHub returned a dataset path without a version directory: {cached_path}"
    )


def _read_revision(data_dir: Path) -> str | None:
    revision_path = data_dir / REVISION_NAME
    if not revision_path.exists():
        return None
    revision = revision_path.read_text(encoding="utf-8").strip()
    return revision or None


def _atomic_write_text(path: Path, value: str) -> None:
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    temporary_path.write_text(value, encoding="utf-8")
    os.replace(temporary_path, path)


def _publish_dataset(cached_path: Path, csv_path: Path) -> None:
    """Copy a validated cache file into durable storage atomically."""
    temporary_path = csv_path.with_suffix(f"{csv_path.suffix}.tmp")
    try:
        shutil.copyfile(cached_path, temporary_path)
        os.replace(temporary_path, csv_path)
    finally:
        temporary_path.unlink(missing_ok=True)


def ensure_dataset(data_dir: Path) -> Path:
    """Ensure the durable AniList CSV exists and record its Kaggle revision."""
    data_dir.mkdir(parents=True, exist_ok=True)
    csv_path = data_dir / CSV_NAME
    if csv_path.exists():
        return csv_path

    logger.info("Downloading AniList dataset (first run)...")
    cached_path, revision = _download_current_dataset()
    _publish_dataset(cached_path, csv_path)
    _atomic_write_text(data_dir / REVISION_NAME, revision)
    return csv_path


def try_refresh_dataset(data_dir: Path) -> bool:
    """Publish the newest Kaggle revision when it differs from durable state.

    KaggleHub's own cache is version-aware. The durable service volume is not,
    so using it as ``output_dir`` eventually raises ``FileExistsError`` when
    the cache marker and CSV disagree. Resolve into KaggleHub's cache instead,
    then copy into the volume only when the upstream revision changes.
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    csv_path = data_dir / CSV_NAME
    cached_path, revision = _download_current_dataset()
    previous_revision = _read_revision(data_dir)

    if csv_path.exists() and previous_revision == revision:
        return False

    # Existing deployments predate the revision sidecar. Avoid an unnecessary
    # index rebuild when their durable CSV already matches the current cache.
    if (
        csv_path.exists()
        and previous_revision is None
        and filecmp.cmp(csv_path, cached_path, shallow=False)
    ):
        _atomic_write_text(data_dir / REVISION_NAME, revision)
        return False

    _publish_dataset(cached_path, csv_path)
    _atomic_write_text(data_dir / REVISION_NAME, revision)
    logger.warning(
        "AniList dataset changed; index will be rebuilt "
        "(old_revision=%s new_revision=%s)",
        previous_revision,
        revision,
    )
    return True
