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
from ja_media_frontend.srt_cleaning.review_alignment import read_alignment_cases
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


def load_review_workspace(
    run: SrtCleanRun, *, alignment_case: Path | None = None
) -> ReviewWorkspace:
    """Join manifest rows, source SRTs, decisions, and cleaned outputs."""

    run_manifest = _read_run_manifest(run)
    return _load_review_artifacts(
        manifest_path=run.manifest_path,
        reconstruct_dir=run.reconstruct_dir,
        source_root=run.run_dir,
        run_id=str(run_manifest.get("run_id", run.run_id)),
        fallback_anilist_id=int(run_manifest.get("anilist_id", run.anilist_id)),
        alignment_case=alignment_case,
    )


def load_review_directory(
    reconstruct_dir: Path, *, alignment_case: Path | None = None
) -> ReviewWorkspace:
    """Load an explicit reconstructed run, including a multi-series corpus."""

    reconstruct_dir = reconstruct_dir.expanduser().resolve()
    if not (reconstruct_dir / "decisions.jsonl").is_file():
        raise FileNotFoundError(
            f"Missing review decisions: {reconstruct_dir / 'decisions.jsonl'}"
        )
    root = reconstruct_dir.parent
    candidates = sorted(root.glob("*.manifest.jsonl"))
    if (reconstruct_dir / "manifest.jsonl").is_file():
        manifest_path = reconstruct_dir / "manifest.jsonl"
    elif (root / "manifest.jsonl").is_file():
        manifest_path = root / "manifest.jsonl"
    elif len(candidates) == 1:
        manifest_path = candidates[0]
    else:
        raise FileNotFoundError(
            f"Expected one manifest JSONL beside {reconstruct_dir}; found {len(candidates)}"
        )
    return _load_review_artifacts(
        manifest_path=manifest_path,
        reconstruct_dir=reconstruct_dir,
        source_root=root,
        run_id=reconstruct_dir.name.removesuffix(".reconstruct"),
        fallback_anilist_id=0,
        alignment_case=alignment_case,
    )


def _load_review_artifacts(
    *,
    manifest_path: Path,
    reconstruct_dir: Path,
    source_root: Path,
    run_id: str,
    fallback_anilist_id: int,
    alignment_case: Path | None = None,
) -> ReviewWorkspace:
    manifest_rows = read_jsonl(manifest_path)
    _validate_manifest_rows(manifest_path, manifest_rows)
    decisions = _read_decisions(reconstruct_dir / "decisions.jsonl")
    alignments = read_alignment_cases(alignment_case)
    by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in manifest_rows:
        by_source[source_key(row)].append(row)

    sources = [
        _load_source(rows, decisions.get(key, {}), source_root, reconstruct_dir, alignments)
        for key, rows in sorted(by_source.items())
    ]
    loaded_sources = tuple(source for source in sources if source.cues)
    first_anilist_id = (
        loaded_sources[0].anilist_id if loaded_sources else fallback_anilist_id
    )
    return ReviewWorkspace(
        anilist_id=first_anilist_id,
        run_id=run_id,
        run_dir=source_root,
        sources=loaded_sources,
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
            reasons=_decision_reasons(row),
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
            served_model=(
                row.get("served_model")
                if isinstance(row.get("served_model"), str)
                else None
            ),
        )
    return decisions


def _decision_reasons(row: dict[str, Any]) -> tuple[str, ...]:
    """Read v2 reasons while keeping old reconstructed runs reviewable."""

    values = row.get("reasons")
    if isinstance(values, list):
        return tuple(value for value in values if isinstance(value, str))
    category = row.get("category")
    return (category,) if isinstance(category, str) else ()


def _load_source(
    rows: list[dict[str, Any]],
    decisions: dict[int, ReviewDecision],
    source_root: Path,
    reconstruct_dir: Path,
    alignments: dict[tuple[str, str], dict[str, Any]],
) -> ReviewSource:
    rows = sorted(rows, key=lambda row: int(row["window_number"]))
    first = rows[0]
    source_path = _resolve_source_path(first, source_root)
    cues = parse_srt(source_path.read_text(encoding="utf-8-sig"), source_path=source_path)
    filename = str(first.get("filename") or Path(str(first["repo_path"])).name)
    cleaned_path = reconstruct_dir / "cleaned" / cleaned_srt_name(first)
    if not cleaned_path.exists():
        cleaned_path = None
    alignment = alignments.get(
        (str(first["subtitle_id"]), str(first["source_sha256"]))
    )
    alignment_by_index = alignment["by_source_index"] if alignment else {}
    review_cues = tuple(
        _review_cue(cue, decisions.get(cue.index), alignment_by_index.get(cue.index))
        for cue in cues
    )
    return ReviewSource(
        anilist_id=int(first["anilist_id"]),
        subtitle_id=str(first["subtitle_id"]),
        repo_path=str(first["repo_path"]),
        filename=filename,
        source_path=source_path,
        cleaned_path=cleaned_path,
        episode_number=_episode_number(first),
        source_sha256=str(first["source_sha256"]),
        cues=review_cues,
        alignment_path=alignment["results_path"] if alignment else None,
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


def _review_cue(cue: SubtitleCue, decision: ReviewDecision | None, alignment=None) -> ReviewCue:
    mechanical = mechanically_normalize_text(cue.text)
    if decision and decision.mechanical_text is not None:
        return ReviewCue(
            original=cue,
            decision=decision,
            mechanical_text=decision.mechanical_text,
            mechanically_changed=decision.mechanically_changed,
            mechanical_rules=decision.mechanical_rules,
            alignment=alignment,
        )
    return ReviewCue(
        original=cue,
        decision=decision,
        mechanical_text=mechanical.text,
        mechanically_changed=mechanical.changed,
        mechanical_rules=mechanical.rules,
        alignment=alignment,
    )
