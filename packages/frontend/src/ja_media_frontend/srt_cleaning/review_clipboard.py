from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
from typing import Any

from ja_media_core.proc import run as run_process
from ja_media_frontend.srt_cleaning.review_models import ReviewCue, ReviewSource, ReviewWorkspace


def copy_review_sample(
    *,
    workspace: ReviewWorkspace,
    source: ReviewSource,
    cue: ReviewCue,
) -> None:
    """Copy a reproducible original-vs-cleaned review sample to the clipboard."""

    command = clipboard_command()
    if command is None:
        raise RuntimeError(clipboard_unavailable_message())
    payload = review_sample_payload(workspace=workspace, source=source, cue=cue)
    try:
        run_process(
            command,
            input=(json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode(
                "utf-8"
            ),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError("clipboard copy failed") from exc


def review_sample_payload(
    *,
    workspace: ReviewWorkspace,
    source: ReviewSource,
    cue: ReviewCue,
) -> dict[str, Any]:
    """Build enough JSON to find and reproduce one review observation."""

    original = cue.original
    decision = cue.decision
    return {
        "schema_name": "ja-media.srt-clean.review-sample",
        "schema_version": "1.0.0",
        "anilist_id": workspace.anilist_id,
        "run_id": workspace.run_id,
        "run_dir": str(workspace.run_dir),
        "episode_number": source.episode_number,
        "subtitle_id": source.subtitle_id,
        "source_sha256": source.source_sha256,
        "repo_path": source.repo_path,
        "source_path": str(source.source_path),
        "cleaned_path": str(source.cleaned_path) if source.cleaned_path else None,
        "cue": {
            "index": original.index,
            "start_s": original.start_s,
            "end_s": original.end_s,
            "original": original.text,
            "mechanical": cue.mechanical_text,
            "mechanically_changed": cue.mechanically_changed,
            "mechanical_rules": list(cue.mechanical_rules),
            "cleaned": cue.display_text,
        },
        "decision": None
        if decision is None
        else {
            "kind": decision.kind,
            "text": decision.text,
            "category": decision.category,
            "custom_id": decision.custom_id,
            "local_id": decision.local_id,
            "window_number": decision.window_number,
            "compliant": decision.compliant,
            "model_text_matches_mechanical": decision.model_text_matches_mechanical,
        },
    }


def clipboard_command() -> list[str] | None:
    system = platform.system()
    if system == "Darwin" and shutil.which("pbcopy") is not None:
        return ["pbcopy"]
    if os.environ.get("WAYLAND_DISPLAY") and shutil.which("wl-copy") is not None:
        return ["wl-copy"]
    return None


def clipboard_unavailable_message() -> str:
    if platform.system() == "Darwin":
        return "pbcopy not found"
    if not os.environ.get("WAYLAND_DISPLAY"):
        return "Wayland clipboard unavailable"
    return "wl-copy not found"
