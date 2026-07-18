"""Phase D acceptance, canonicalization, and subtitle-LID compilers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
import hashlib
import json

from ja_media_core.subtitle_lid import (
    LineLanguageDetector,
    SubtitleLanguageIdConfig,
    analyze_subtitle_language,
)
from ja_media_core.transcripts import parse_ass, parse_srt

from ja_media_data.bronze_store import BronzeStore
from ja_media_data.canonicalization import (
    build_canonical_rows,
    select_canonical_candidates,
)
from ja_media_data.lakehouse.repository import DuckLakeRepository
from ja_media_data.execution import StageExecution
from ja_media_data.pipeline_repository import PipelineRepository
from ja_media_data.pipeline_types import (
    AcceptedBinding,
    StageResult,
    SubtitleLanguageResult,
)


ACCEPTANCE_POLICY_VERSION = "accept-resolver-proposals-v1"
CANONICALIZATION_POLICY_VERSION = "latest-manifest-modified-v1"
SUBTITLE_LID_RECIPE_VERSION = "subtitle-script-fasttext-v1"


def run_phase_d(
    repository: DuckLakeRepository,
    store: BronzeStore,
    *,
    config: SubtitleLanguageIdConfig | None = None,
    detector: LineLanguageDetector | None = None,
) -> tuple[StageResult, StageResult, StageResult]:
    """Run the subtitle-LID target closure in dependency order."""

    pipeline = PipelineRepository(repository.connection)
    run_id = pipeline.start_pipeline_run(
        "subtitle_lid", forced_from_stage=None,
        override_revision=binding_override_revision(repository),
    )
    try:
        acceptance = compile_acceptances(repository, pipeline_run_id=run_id, ordinal=1)
        canonical = compile_canonical_inputs(
            repository, store, pipeline_run_id=run_id, ordinal=2
        )
        lid = compile_subtitle_lid(
            repository, store, config=config, detector=detector,
            pipeline_run_id=run_id, ordinal=3,
        )
    except Exception as error:
        pipeline.finish_pipeline_run(run_id, error=error)
        raise
    pipeline.finish_pipeline_run(run_id)
    return acceptance, canonical, lid


def compile_acceptances(
    repository: DuckLakeRepository, *, pipeline_run_id: str | None = None,
    ordinal: int = 1, force: bool = False,
) -> StageResult:
    """Accept every resolver proposal under the intentionally permissive v1 policy."""

    pipeline = PipelineRepository(repository.connection)
    input_heads = pipeline.input_heads("episode_resolution")

    def compile_stage(execution: StageExecution) -> StageResult:
        source = repository.connection.execute(
            """SELECT proposal_id, namespace, series_id, episode, audio_capture_id,
                      input_data_version, recipe_version
               FROM episode_binding_proposals
               ORDER BY namespace, series_id, episode, audio_capture_id, proposal_id"""
        ).fetchall()
        fingerprint = _fingerprint(ACCEPTANCE_POLICY_VERSION, source)
        output = [AcceptedBinding(
            _stable_id("acceptance", row[0], ACCEPTANCE_POLICY_VERSION),
            row[0], row[1], row[2], row[3], row[4], "automatic",
            ACCEPTANCE_POLICY_VERSION, _fingerprint(*row),
        ) for row in source]
        return pipeline.replace_acceptances(output, fingerprint, execution)

    return _execute_stage(
        pipeline, repository, "accepted_bindings", ACCEPTANCE_POLICY_VERSION,
        input_heads, compile_stage, pipeline_run_id=pipeline_run_id,
        ordinal=ordinal, force=force, output_table="accepted_bindings_auto",
    )


def compile_canonical_inputs(
    repository: DuckLakeRepository, store: BronzeStore, *,
    pipeline_run_id: str | None = None, ordinal: int = 1, force: bool = False,
) -> StageResult:
    """Select the latest accepted capture per locator and enumerate its subtitles."""

    pipeline = PipelineRepository(repository.connection)
    input_heads = pipeline.input_heads("accepted_bindings", "bronze_captures")
    input_heads["binding_overrides"] = {"revision": binding_override_revision(repository)}

    def compile_stage(execution: StageExecution) -> StageResult:
        selected = select_canonical_candidates(repository)
        fingerprint = _fingerprint(
            CANONICALIZATION_POLICY_VERSION, [asdict(item) for item in selected]
        )
        episodes, subtitles = build_canonical_rows(
            selected, store, fingerprint=_fingerprint, stable_id=_stable_id
        )
        return pipeline.replace_canonical_inputs(
            episodes, subtitles, fingerprint, execution
        )

    return _execute_stage(
        pipeline, repository, "canonical_inputs", CANONICALIZATION_POLICY_VERSION,
        input_heads, compile_stage, pipeline_run_id=pipeline_run_id,
        ordinal=ordinal, force=force, output_table="canonical_episode_inputs",
    )


def compile_subtitle_lid(
    repository: DuckLakeRepository,
    store: BronzeStore,
    *,
    config: SubtitleLanguageIdConfig | None = None,
    detector: LineLanguageDetector | None = None,
    pipeline_run_id: str | None = None,
    ordinal: int = 1,
    force: bool = False,
) -> StageResult:
    """Classify every canonical subtitle and atomically replace the result product."""

    pipeline = PipelineRepository(repository.connection)
    input_heads = pipeline.input_heads("canonical_inputs")

    def compile_stage(execution: StageExecution) -> StageResult:
        options = config or SubtitleLanguageIdConfig()
        source = repository.connection.execute(
            """SELECT subtitle_input_id, namespace, series_id, episode,
                      audio_capture_id, object_key, codec, input_fingerprint
               FROM canonical_subtitle_inputs ORDER BY subtitle_input_id"""
        ).fetchall()
        fingerprint = _fingerprint(
            SUBTITLE_LID_RECIPE_VERSION, options.model_dump(mode="json"),
            [(row[0], row[7]) for row in source],
        )
        results = [
            _analyze_subtitle(row, store, options, detector) for row in source
        ]
        return pipeline.replace_lid_results(results, fingerprint, execution)

    return _execute_stage(
        pipeline, repository, "subtitle_lid", SUBTITLE_LID_RECIPE_VERSION,
        input_heads, compile_stage, pipeline_run_id=pipeline_run_id,
        ordinal=ordinal, force=force, output_table="subtitle_language_results",
    )


def _analyze_subtitle(
    row: tuple[object, ...], store: BronzeStore,
    config: SubtitleLanguageIdConfig,
    detector: LineLanguageDetector | None,
) -> SubtitleLanguageResult:
    key = str(row[5])
    text = store.read_text(key)
    codec = str(row[6] or "").casefold()
    if key.casefold().endswith(".srt"):
        cues = parse_srt(text, source_path=key)
    elif codec in {"ass", "ssa"}:
        cues = parse_ass(text, source_path=key)
    else:
        cues = parse_srt(text, source_path=key)
    analysis = analyze_subtitle_language(cues, config=config, detector=detector)
    return SubtitleLanguageResult(
        str(row[0]),
        str(row[1]),
        str(row[2]),
        str(row[3]),
        str(row[4]),
        analysis.language.value,
        analysis.reason,
        asdict(analysis.script),
        asdict(analysis.sampled) if analysis.sampled else None,
        str(row[7]),
        SUBTITLE_LID_RECIPE_VERSION,
    )


def _execute_stage(
    pipeline: PipelineRepository,
    repository: DuckLakeRepository,
    target: str,
    recipe_version: str,
    input_heads: dict[str, object],
    operation: Callable[[StageExecution], StageResult],
    *, pipeline_run_id: str | None, ordinal: int, force: bool,
    output_table: str,
) -> StageResult:
    own_run = pipeline_run_id is None
    run_id = pipeline_run_id or pipeline.start_pipeline_run(
        target, forced_from_stage=target if force else None,
        override_revision=binding_override_revision(repository),
    )
    execution = pipeline.begin_stage(
        run_id, target, ordinal, recipe_version, input_heads
    )
    head = pipeline.current_head(target)
    if not force and head and head.build_key == execution.build_key:
        rows = int(repository.connection.execute(
            f"SELECT count(*) FROM {output_table}"
        ).fetchone()[0])
        pipeline.finish_stage(
            execution, disposition="reused", materialization_id=head.materialization_id
        )
        result = StageResult(
            target, False, head.fingerprint, rows, execution.attempt_id,
            run_id, head.materialization_id,
        )
        if own_run:
            pipeline.finish_pipeline_run(run_id)
        return result
    try:
        result = operation(execution)
    except Exception as error:
        pipeline.finish_stage(execution, disposition="failed", error=error)
        if own_run:
            pipeline.finish_pipeline_run(run_id, error=error)
        raise
    pipeline.finish_stage(
        execution, disposition="succeeded",
        materialization_id=result.materialization_id,
    )
    if own_run:
        pipeline.finish_pipeline_run(run_id)
    return result


def binding_override_revision(repository: DuckLakeRepository) -> int:
    """Return the durable override-head revision used by build and cache keys."""
    source = repository.override_repository
    if source is None:
        return 0
    revision = getattr(source, "current_revision", None)
    if revision is not None:
        return int(revision())
    rows = [
        (item.override_id, item.namespace, item.series_id, item.episode,
         item.audio_capture_id)
        for item in source.iter_current_overrides()
    ]
    return int(_fingerprint(rows)[:15], 16)


def _fingerprint(*parts: object) -> str:
    payload = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def _stable_id(prefix: str, *parts: str) -> str:
    return prefix + "-" + hashlib.sha256("\0".join(parts).encode()).hexdigest()[:32]
