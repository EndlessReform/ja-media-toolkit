"""Deterministic compiler for canonical subtitle language evidence."""

from __future__ import annotations

from dataclasses import asdict, dataclass

import duckdb
from ja_media_core.subtitle_lid import (
    LineLanguageDetector,
    SubtitleLanguageIdConfig,
    analyze_subtitle_language,
)
from ja_media_core.transcripts import parse_ass, parse_srt

from ja_media_data.products.identities import fingerprint
from ja_media_data.products.subtitle_lid.models import SubtitleLanguageResult
from ja_media_data.storage.bronze import BronzeStore


SUBTITLE_LID_RECIPE_VERSION = "subtitle-script-fasttext-v1"


@dataclass(frozen=True)
class CompiledSubtitleLid:
    """A complete subtitle-LID table prepared for one atomic commit."""

    rows: tuple[SubtitleLanguageResult, ...]
    fingerprint: str


def compile_product(
    connection: duckdb.DuckDBPyConnection,
    store: BronzeStore,
    *,
    config: SubtitleLanguageIdConfig | None = None,
    detector: LineLanguageDetector | None = None,
) -> CompiledSubtitleLid:
    """Classify every canonical subtitle from one stable source query."""

    options = config or SubtitleLanguageIdConfig()
    source = connection.execute(
        """SELECT subtitle_input_id, namespace, series_id, episode,
                  audio_capture_id, object_key, codec, input_fingerprint
           FROM canonical_subtitle_inputs ORDER BY subtitle_input_id"""
    ).fetchall()
    product_fingerprint = fingerprint(
        SUBTITLE_LID_RECIPE_VERSION,
        options.model_dump(mode="json"),
        [(row[0], row[7]) for row in source],
    )
    rows = tuple(_analyze(row, store, options, detector) for row in source)
    return CompiledSubtitleLid(rows, product_fingerprint)


def _analyze(
    row: tuple[object, ...],
    store: BronzeStore,
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
