"""Parallel Gate 1 survey using only the existing identity-time scorers."""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
import json
from pathlib import Path
import shutil
import tempfile
from time import perf_counter
import duckdb

from ja_media_core.subsync import (
    subtitle_anchor_fit_score,
    subtitle_goodness_of_fit,
)
from ja_media_core.transcripts import SubtitleCue, parse_ass, parse_srt

from subtitle_alignment.identity_store import write_identity_results


SURVEY_VERSION = "identity-v3"


@dataclass(frozen=True)
class EpisodeTask:
    dataset: str
    anilist_id: int
    episode: int
    anchors: tuple[dict[str, object], ...]
    candidates: tuple[dict[str, object], ...]


@dataclass(frozen=True)
class ParsedTrack:
    cues: tuple[SubtitleCue, ...]
    cue_count: int
    active_s: float
    span_s: float
    parse_ms: float
    error: str | None = None


def run_identity_survey(
    dataset: Path, *, output_root: Path, workers: int
) -> Path:
    """Score every cached anchor/candidate pair with no timing transform."""

    manifest = json.loads((dataset / "manifest.json").read_text())
    result_name = f"gate1-{SURVEY_VERSION}-{manifest['dataset_id']}"
    output_root.mkdir(parents=True, exist_ok=True)
    target = output_root / result_name
    if (target / "summary.json").is_file():
        return target
    if target.exists():
        raise FileExistsError(f"incomplete result directory exists: {target}")
    temporary = Path(tempfile.mkdtemp(prefix=f".{result_name}-", dir=output_root))
    started = perf_counter()
    try:
        tasks = _episode_tasks(dataset)
        with ProcessPoolExecutor(max_workers=workers) as pool:
            batches = pool.map(_score_episode, tasks, chunksize=4)
            results = [row for batch in batches for row in batch]
        results.sort(
            key=lambda row: (
                row["anilist_id"], row["episode"], row["anchor_stream_index"],
                row["candidate_id"],
            )
        )
        elapsed_s = perf_counter() - started
        write_identity_results(
            temporary,
            dataset=dataset,
            manifest=manifest,
            results=results,
            workers=workers,
            elapsed_s=elapsed_s,
            survey_version=SURVEY_VERSION,
        )
        temporary.replace(target)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return target


def _episode_tasks(dataset: Path) -> list[EpisodeTask]:
    connection = duckdb.connect(str(dataset / "evaluation.duckdb"), read_only=True)
    try:
        rows = connection.execute(
            """SELECT cast(anchor.anilist_id AS BIGINT),
                      cast(anchor.episode AS INTEGER), anchor.subtitle_input_id,
                      cast(anchor.stream_index AS INTEGER), anchor.codec,
                      anchor.object_key, anchor.relative_path,
                      candidate.subtitle_id,
                      candidate.repo_path, candidate.extension,
                      candidate.relative_path, candidate.metadata_json
                 FROM embedded_subtitles AS anchor
                 JOIN kitsunekko_candidates AS candidate
                   ON candidate.anilist_id = anchor.anilist_id
                  AND candidate.canonical_episode = anchor.episode
                WHERE candidate.fetch_status = 'ok'
                ORDER BY 1, 2, 4, 7"""
        ).fetchall()
    finally:
        connection.close()
    grouped: dict[tuple[int, int], dict[str, dict[str, object]]] = {}
    for row in rows:
        key = int(row[0]), int(row[1])
        item = grouped.setdefault(key, {"anchors": {}, "candidates": {}})
        item["anchors"].setdefault(
            str(row[2]),
            {
                "id": str(row[2]), "stream_index": int(row[3]),
                "source_codec": str(row[4]),
                "format": Path(str(row[5])).suffix.lstrip(".").lower(),
                "path": str(row[6]),
            },
        )
        metadata = json.loads(str(row[11]))
        item["candidates"].setdefault(
            str(row[7]),
            {
                "id": str(row[7]), "repo_path": str(row[8]),
                "format": str(row[9]), "path": str(row[10]),
                "group_hint": metadata.get("group_hint"),
                "at_x_hint": "at-x" in str(row[8]).casefold(),
            },
        )
    return [
        EpisodeTask(
            dataset=str(dataset), anilist_id=key[0], episode=key[1],
            anchors=tuple(value["anchors"].values()),
            candidates=tuple(value["candidates"].values()),
        )
        for key, value in grouped.items()
    ]


def _score_episode(task: EpisodeTask) -> list[dict[str, object]]:
    root = Path(task.dataset)
    anchors = {str(item["id"]): _parse_track(root, item) for item in task.anchors}
    candidates = {
        str(item["id"]): _parse_track(root, item) for item in task.candidates
    }
    rows = []
    for anchor_item in task.anchors:
        anchor = anchors[str(anchor_item["id"])]
        for candidate_item in task.candidates:
            candidate = candidates[str(candidate_item["id"])]
            row = _base_row(task, anchor_item, candidate_item, anchor, candidate)
            if anchor.error or candidate.error:
                row.update(
                    status="parse_error",
                    failure_side=("anchor" if anchor.error else "candidate"),
                    error=anchor.error or candidate.error,
                )
            else:
                started = perf_counter()
                row.update(
                    status="scored", failure_side=None, error=None,
                    goodness_of_fit=subtitle_goodness_of_fit(
                        anchor.cues, candidate.cues
                    ),
                    anchor_fit_score=subtitle_anchor_fit_score(
                        anchor.cues, candidate.cues
                    ),
                )
                row["score_ms"] = (perf_counter() - started) * 1000
            rows.append(row)
    return rows


def _parse_track(root: Path, item: dict[str, object]) -> ParsedTrack:
    started = perf_counter()
    try:
        text = (root / str(item["path"])).read_bytes().decode(
            "utf-8-sig", errors="replace"
        )
        format_name = str(item["format"]).lower()
        cues = parse_srt(text) if format_name in {"srt", "subrip"} else parse_ass(text)
        cue_tuple = tuple(cues)
        active_s = sum(max(0.0, cue.duration_s) for cue in cue_tuple)
        span_s = max((cue.end_s for cue in cue_tuple), default=0.0) - min(
            (cue.start_s for cue in cue_tuple), default=0.0
        )
        return ParsedTrack(
            cue_tuple, len(cue_tuple), active_s, span_s,
            (perf_counter() - started) * 1000,
        )
    except (OSError, UnicodeError, ValueError) as error:
        return ParsedTrack(
            (), 0, 0.0, 0.0, (perf_counter() - started) * 1000,
            f"{type(error).__name__}: {str(error)[:240]}",
        )


def _base_row(
    task: EpisodeTask,
    anchor_item: dict[str, object],
    candidate_item: dict[str, object],
    anchor: ParsedTrack,
    candidate: ParsedTrack,
) -> dict[str, object]:
    return {
        "anilist_id": task.anilist_id, "episode": task.episode,
        "anchor_id": anchor_item["id"],
        "anchor_stream_index": anchor_item["stream_index"],
        "anchor_codec": anchor_item["source_codec"],
        "anchor_serialization": anchor_item["format"],
        "candidate_id": candidate_item["id"],
        "candidate_repo_path": candidate_item["repo_path"],
        "candidate_extension": candidate_item["format"],
        "group_hint": candidate_item["group_hint"],
        "at_x_hint": candidate_item["at_x_hint"],
        "anchor_cues": anchor.cue_count, "candidate_cues": candidate.cue_count,
        "anchor_active_s": anchor.active_s,
        "candidate_active_s": candidate.active_s,
        "anchor_span_s": anchor.span_s, "candidate_span_s": candidate.span_s,
        "anchor_parse_ms": anchor.parse_ms,
        "candidate_parse_ms": candidate.parse_ms,
        "score_ms": None, "goodness_of_fit": None, "anchor_fit_score": None,
    }
