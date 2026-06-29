from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import re
from typing import Any

from ja_media_core.subsync import infer_episode_number
from ja_media_core.transcripts import SubtitleCue, parse_srt

from ja_media_frontend.srt_cleaning.batch import read_jsonl
from ja_media_frontend.srt_cleaning.normalization import mechanically_normalize_text
from ja_media_frontend.srt_cleaning.review_models import (
    ReviewCue,
    ReviewDecision,
    ReviewSource,
    ReviewWorkspace,
)
from ja_media_frontend.srt_cleaning.source_rebuild import (
    cleaned_srt_name,
    source_key,
)
from ja_media_frontend.srt_cleaning.workspace import (
    RUN_SCHEMA_NAME,
    RUN_SCHEMA_VERSION,
    WINDOW_SCHEMA_NAME,
    WINDOW_SCHEMA_VERSION,
    SrtCleanRun,
    validate_schema_major,
)


def load_review_workspace(run: SrtCleanRun) -> ReviewWorkspace:
    """Join manifest rows, source SRTs, decisions, and cleaned outputs."""

    run_manifest = _read_run_manifest(run)
    manifest_rows = read_jsonl(run.manifest_path)
    _validate_manifest_rows(run.manifest_path, manifest_rows)
    decisions = _read_decisions(run.reconstruct_dir / "decisions.jsonl")
    by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in manifest_rows:
        by_source[source_key(row)].append(row)

    sources = [
        _load_source(rows, decisions.get(key, {}), run.run_dir)
        for key, rows in sorted(by_source.items())
    ]
    return ReviewWorkspace(
        anilist_id=int(run_manifest.get("anilist_id", run.anilist_id)),
        run_id=str(run_manifest.get("run_id", run.run_id)),
        run_dir=run.run_dir,
        sources=tuple(source for source in sources if source.cues),
    )


def _read_run_manifest(run: SrtCleanRun) -> dict[str, Any]:
    if not run.run_manifest_path.exists():
        return {"anilist_id": run.anilist_id, "run_id": run.run_id}
    import json

    payload = json.loads(run.run_manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{run.run_manifest_path} is not a JSON object")
    validate_schema_major(
        schema_name=payload.get("schema_name"),
        schema_version=payload.get("schema_version"),
        expected_name=RUN_SCHEMA_NAME,
        expected_version=RUN_SCHEMA_VERSION,
        artifact=run.run_manifest_path,
    )
    return payload


def _validate_manifest_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    for row in rows:
        validate_schema_major(
            schema_name=row.get("schema_name"),
            schema_version=row.get("schema_version"),
            expected_name=WINDOW_SCHEMA_NAME,
            expected_version=WINDOW_SCHEMA_VERSION,
            artifact=path,
        )


def _read_decisions(path: Path) -> dict[str, dict[int, ReviewDecision]]:
    decisions: dict[str, dict[int, ReviewDecision]] = defaultdict(dict)
    if not path.exists():
        return {}
    for row in read_jsonl(path):
        source = str(row.get("source_key") or "")
        index = row.get("index")
        if not source or index is None:
            continue
        decisions[source][int(index)] = ReviewDecision(
            kind=str(row.get("decision") or "missing"),
            text=row.get("text") if isinstance(row.get("text"), str) else None,
            category=(
                row.get("category") if isinstance(row.get("category"), str) else None
            ),
            custom_id=str(row["custom_id"]) if row.get("custom_id") else None,
            local_id=int(row["id"]) if row.get("id") is not None else None,
            window_number=(
                int(row["window_number"]) if row.get("window_number") is not None else None
            ),
            compliant=bool(row.get("compliant", True)),
            mechanical_text=(
                row.get("mechanical_text")
                if isinstance(row.get("mechanical_text"), str)
                else None
            ),
            mechanically_changed=bool(row.get("mechanically_changed", False)),
            mechanical_rules=tuple(
                value
                for value in row.get("mechanical_rules", [])
                if isinstance(value, str)
            ),
            model_text_matches_mechanical=(
                row.get("model_text_matches_mechanical")
                if isinstance(row.get("model_text_matches_mechanical"), bool)
                else None
            ),
        )
    return decisions


def _load_source(
    rows: list[dict[str, Any]],
    decisions: dict[int, ReviewDecision],
    run_dir: Path,
) -> ReviewSource:
    rows = sorted(rows, key=lambda row: int(row["window_number"]))
    first = rows[0]
    source_path = _resolve_source_path(first, run_dir)
    cues = parse_srt(source_path.read_text(encoding="utf-8-sig"), source_path=source_path)
    filename = str(first.get("filename") or Path(str(first["repo_path"])).name)
    cleaned_path = run_dir / "reconstruct" / "cleaned" / cleaned_srt_name(first)
    if not cleaned_path.exists():
        cleaned_path = None
    review_cues = tuple(_review_cue(cue, decisions.get(cue.index)) for cue in cues)
    return ReviewSource(
        subtitle_id=str(first["subtitle_id"]),
        repo_path=str(first["repo_path"]),
        filename=filename,
        source_path=source_path,
        cleaned_path=cleaned_path,
        episode_number=_episode_number(first),
        source_sha256=str(first["source_sha256"]),
        cues=review_cues,
    )


def _resolve_source_path(row: dict[str, Any], run_dir: Path) -> Path:
    """Resolve cached source SRTs even when manifests came from another host."""

    raw_path = Path(str(row["local_cache_path"])).expanduser()
    candidates = [
        raw_path,
        run_dir / "sources" / raw_path.name,
        run_dir
        / "sources"
        / f"{row['subtitle_id']}.{str(row['source_sha256'])[:12]}.srt",
    ]
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.is_file():
            return resolved

    matches = sorted((run_dir / "sources").glob(f"{row['subtitle_id']}.*.srt"))
    if len(matches) == 1:
        return matches[0].resolve()
    expected = candidates[-1]
    raise FileNotFoundError(
        f"Could not find cached source SRT for subtitle {row['subtitle_id']} "
        f"under {run_dir / 'sources'}; expected {expected.name}"
    )


def _episode_number(row: dict[str, Any]) -> int | None:
    for value in (row.get("repo_path"), row.get("filename")):
        if isinstance(value, str) and (episode := infer_episode_number(value)):
            return episode
        if isinstance(value, str) and (episode := _trailing_episode_number(value)):
            return episode
    return None


def _trailing_episode_number(value: str) -> int | None:
    """Infer simple subtitle names like ``Title - 01.srt``."""

    stem = Path(value).stem
    match = re.search(r"(?:^|[\s._-])(\d{1,4})(?:v\d+)?$", stem)
    if match is None:
        return None
    episode = int(match.group(1))
    return episode if episode > 0 else None


def _review_cue(cue: SubtitleCue, decision: ReviewDecision | None) -> ReviewCue:
    mechanical = mechanically_normalize_text(cue.text)
    if decision and decision.mechanical_text is not None:
        return ReviewCue(
            original=cue,
            decision=decision,
            mechanical_text=decision.mechanical_text,
            mechanically_changed=decision.mechanically_changed,
            mechanical_rules=decision.mechanical_rules,
        )
    return ReviewCue(
        original=cue,
        decision=decision,
        mechanical_text=mechanical.text,
        mechanically_changed=mechanical.changed,
        mechanical_rules=mechanical.rules,
    )
