from __future__ import annotations

import argparse
import asyncio
import glob
import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ja_media_apple.audio_chunks import select_audio_chunk
from ja_media_apple.asr_config import (
    build_selected_asr_backend,
    load_apple_asr_config,
)
from ja_media_apple.vad import DEFAULT_MLX_AUDIO_VAD_MODEL, MlxAudioVadBackend
from ja_media_apple.vad_cli import _dump_audio_chunks, run_vad_local
from ja_media_core.asr import AsrRuntimeOptions, asr_request_from_chunks
from ja_media_core.audio import (
    AudioChunk,
    full_audio_chunk,
    probe_audio_source,
    resolve_audio_source,
)
from ja_media_core.config import load_config
from ja_media_core.transcripts import SubtitleCue, format_srt
from ja_media_core.vad import (
    VadOptions,
    plan_vad_splits,
)


_LOG = logging.getLogger("ja_media_apple")


def main() -> None:
    from ja_media_frontend.cli import main as frontend_main

    frontend_main()


def run_transcribe(args: argparse.Namespace) -> None:
    _LOG.info("[bold]Loading ASR config[/bold]")
    app_config = load_config(args.config, required=True)
    asr_config = load_apple_asr_config(args.config, required=True)
    backend_config = asr_config.get_backend_config(args.backend)
    vad_options = app_config.vad.to_options()
    input_paths = _expand_input_patterns(args.input)
    _LOG.info(
        "Resolved [bold cyan]%d[/bold cyan] input file(s)",
        len(input_paths),
    )
    if args.srt_dir and not args.startup_only:
        _ensure_unique_srt_input_stems(input_paths)

    _LOG.info("Planning ASR chunks with VAD before backend startup")
    prepared = [
        _prepare_transcribe_input(
            input_path,
            asr_config=asr_config,
            backend_config=backend_config,
            vad_options=vad_options,
            args=args,
        )
        for input_path in input_paths
    ]
    chunk_count = sum(len(request.chunks) for _, request, _ in prepared)
    total_duration_s = sum(
        chunk.duration_s
        for _, request, _ in prepared
        for chunk in request.chunks
    )
    _LOG.info(
        "Prepared [bold cyan]%d[/bold cyan] ASR chunk(s), %.1f minutes total",
        chunk_count,
        total_duration_s / 60.0,
    )

    _LOG.info(
        "Loading backend [bold]%s[/bold] (%s)",
        args.backend or asr_config.default_backend,
        backend_config.type,
    )
    backend = build_selected_asr_backend(asr_config, name=args.backend)
    payloads = []
    for payload, request, runtime_options in prepared:
        payload["backend"] = {
            "selected": args.backend or asr_config.default_backend,
            "type": backend_config.type,
            "name": backend.name,
            "metadata": getattr(backend, "metadata", {}),
        }
        payloads.append(payload)

    if not args.startup_only:
        runtime_options = prepared[0][2]
        max_concurrent = runtime_options.backend_options.get(
            "max_concurrent_requests",
            1,
        )
        _LOG.info(
            "Submitting to ASR backend with max_concurrent_requests=%s",
            max_concurrent,
        )
        asyncio.run(
            _transcribe_prepared_async(
                backend,
                prepared=prepared,
                args=args,
            )
        )

    if args.srt_dir and not args.startup_only:
        srt_dir = Path(args.srt_dir).expanduser()
        _LOG.info("Writing SRT files to %s", srt_dir)
        for payload in payloads:
            payload["srt_path"] = str(_write_srt_payload(payload, output_dir=srt_dir))

    output_payload = payloads[0] if len(payloads) == 1 else {"results": payloads}
    if args.format == "json":
        print(json.dumps(output_payload, indent=2, sort_keys=True))
    else:
        if len(payloads) == 1:
            print(_asr_payload_text(payloads[0], startup_only=args.startup_only))
        else:
            print(
                "\n\n".join(
                    _asr_payload_text(payload, startup_only=args.startup_only)
                    for payload in payloads
                )
            )


async def _transcribe_prepared_async(
    backend: Any,
    *,
    prepared: list[tuple[dict[str, Any], Any, AsrRuntimeOptions]],
    args: argparse.Namespace,
) -> None:
    all_chunks = [
        chunk
        for _, request, _ in prepared
        for chunk in request.chunks
    ]
    runtime_options = prepared[0][2]
    combined_request = asr_request_from_chunks(
        all_chunks,
        language=args.language,
        context=args.context_info,
        hotwords=args.hotword,
        metadata={
            "source_file_count": len(prepared),
            "source_chunk_count": len(all_chunks),
        },
    )
    if hasattr(backend, "transcribe_async"):
        transcripts = await backend.transcribe_async(
            combined_request,
            runtime_options=runtime_options,
        )
    else:
        transcripts = await asyncio.to_thread(
            backend.transcribe,
            combined_request,
            runtime_options=runtime_options,
        )

    cursor = 0
    for payload, request, _ in prepared:
        count = len(request.chunks)
        payload_transcripts = transcripts[cursor : cursor + count]
        cursor += count
        payload["status"] = "succeeded"
        payload["transcripts"] = [
            asdict(transcript) for transcript in payload_transcripts
        ]
        payload["rejoined"] = _rejoin_transcripts(payload["transcripts"])


def _prepare_transcribe_input(
    input_path: Path,
    *,
    asr_config: Any,
    backend_config: Any,
    vad_options: VadOptions,
    args: argparse.Namespace,
) -> tuple[dict[str, Any], Any, AsrRuntimeOptions]:
    source = resolve_audio_source(input_path, base_dir=Path.cwd(), must_exist=True)
    if source.kind != "client-local":
        raise SystemExit("transcribe only supports client-local files for now")

    audio_format = probe_audio_source(source)
    full_chunk = full_audio_chunk(
        source,
        audio_format,
        kind="asr_input",
        metadata={"purpose": "local_asr"},
    )
    chunk = select_audio_chunk(full_chunk, start_s=args.start_s, end_s=args.end_s)
    asr_chunks = _plan_asr_chunks(chunk, backend_config, vad_options=vad_options)
    request = asr_request_from_chunks(
        asr_chunks,
        language=args.language,
        context=args.context_info,
        hotwords=args.hotword,
        metadata={
            "config_backend": args.backend or asr_config.default_backend,
            "source_chunk_count": len(asr_chunks),
        },
    )
    backend_options = (
        backend_config.runtime_backend_options()
        if hasattr(backend_config, "runtime_backend_options")
        else {}
    )
    if args.max_concurrent_requests is not None:
        if args.max_concurrent_requests < 1:
            raise SystemExit("--max-concurrent-requests must be at least 1")
        backend_options["max_concurrent_requests"] = args.max_concurrent_requests

    runtime_options = AsrRuntimeOptions(
        timeout_s=getattr(backend_config, "timeout_s", None),
        backend_options=backend_options,
    )

    payload = {
        "source": asdict(source),
        "format": asdict(audio_format),
        "chunk": asdict(chunk),
        "asr_chunks": [asdict(asr_chunk) for asr_chunk in asr_chunks],
        "pipeline": {
            "vad_split_enabled": len(asr_chunks) > 1,
            "vad_options": asdict(vad_options),
            "target_split_s": getattr(backend_config, "target_split_s", None),
            "split_search_radius_s": getattr(
                backend_config,
                "split_search_radius_s",
                None,
            ),
            "rejoin_overlap_s": getattr(backend_config, "rejoin_overlap_s", None),
        },
        "request": {
            "language": request.language,
            "task": request.task,
            "context": request.context,
            "hotwords": list(request.hotwords),
            "timestamps": request.timestamps,
            "diarization": request.diarization,
            "metadata": request.metadata,
        },
        "backend": None,
        "runtime_options": {
            "timeout_s": runtime_options.timeout_s,
            "backend_options": runtime_options.backend_options,
        },
        "status": "startup_ok",
    }

    return payload, request, runtime_options


def _plan_asr_chunks(
    chunk: AudioChunk,
    backend_config: Any,
    *,
    vad_options: VadOptions,
) -> list[AudioChunk]:
    target_split_s = getattr(backend_config, "target_split_s", None)
    if target_split_s is None or chunk.duration_s <= target_split_s:
        return [chunk]

    vad_backend = MlxAudioVadBackend(
        model_id=getattr(backend_config, "vad_model_id", DEFAULT_MLX_AUDIO_VAD_MODEL),
    )
    return plan_vad_splits(
        chunk,
        backend=vad_backend,
        every_s=target_split_s,
        search_radius_s=getattr(backend_config, "split_search_radius_s", 45.0),
        vad_options=vad_options,
        prefer_before_target=getattr(
            backend_config,
            "prefer_split_before_target",
            False,
        ),
        kind="asr_chunk",
        metadata={
            "purpose": "configured_vad_asr_split",
            "target_split_s": target_split_s,
            "rejoin_overlap_s": getattr(backend_config, "rejoin_overlap_s", 0.0),
        },
    )


def _expand_input_patterns(patterns: list[str]) -> list[Path]:
    paths: list[Path] = []
    seen: set[Path] = set()
    for pattern in patterns:
        expanded_pattern = str(Path(pattern).expanduser())
        if glob.has_magic(expanded_pattern):
            matches = sorted(
                Path(match).resolve()
                for match in glob.glob(expanded_pattern, recursive=True)
                if Path(match).is_file()
            )
            if not matches:
                raise SystemExit(f"No files matched transcribe input pattern: {pattern}")
        else:
            matches = [Path(pattern).expanduser().resolve()]

        for path in matches:
            if path in seen:
                continue
            if not path.is_file():
                raise SystemExit(f"transcribe input is not a file: {path}")
            seen.add(path)
            paths.append(path)

    if not paths:
        raise SystemExit("No transcribe inputs were provided")
    return paths


def _asr_payload_text(payload: dict[str, Any], *, startup_only: bool) -> str:
    if startup_only:
        return _asr_startup_text(payload)
    return _asr_transcript_text(payload)


def _asr_startup_text(payload: dict[str, Any]) -> str:
    metadata = payload["backend"]["metadata"]
    model_metadata = metadata.get("model_metadata", {})
    load_counts = model_metadata.get("load_counts", {})
    lines = [
        f"source: {payload['source']['locator']}",
        f"backend: {payload['backend']['selected']} ({payload['backend']['type']})",
        f"model: {metadata.get('vllm_model', '<unset>')}",
        f"url: {metadata.get('vllm_base_url', '<unset>')}",
        f"audio_model_loaded: {metadata.get('model_loaded', False)}",
        f"transformers: {model_metadata.get('transformers_version', '<not loaded>')}",
        f"weights: {model_metadata.get('weights_path', '<not loaded>')}",
        f"load_counts: {load_counts}",
        f"chunk: {payload['chunk']['start_s']:.3f}s-{payload['chunk']['end_s']:.3f}s",
        f"asr_chunks: {len(payload['asr_chunks'])}",
        "status: startup_ok",
    ]
    for item in payload["asr_chunks"]:
        metadata = item["metadata"]
        lines.append(
            f"  chunk {item['metadata'].get('split_index', 0)}: "
            f"{item['start_s']:.3f}s-{item['end_s']:.3f}s "
            f"next_target={metadata.get('next_target_s')} "
            f"fallback={metadata.get('next_cut_fallback')} "
            f"reason={metadata.get('next_cut_reason')}"
        )
    return "\n".join(lines)


def _write_srt_payload(payload: dict[str, Any], *, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    source = payload["source"]
    source_path = Path(source["locator"])
    output_path = output_dir / f"{source_path.stem}.srt"
    output_path.write_text(_payload_to_srt(payload), encoding="utf-8")
    return output_path


def _ensure_unique_srt_input_stems(paths: list[Path]) -> None:
    stems: dict[str, str] = {}
    for path in paths:
        existing = stems.get(path.stem)
        if existing is not None:
            raise SystemExit(
                "Cannot write SRT files with identical stems into one directory: "
                f"{existing} and {path}"
            )
        stems[path.stem] = str(path)


def _payload_to_srt(payload: dict[str, Any]) -> str:
    return format_srt(_srt_cues_from_payload(payload))


def _srt_cues_from_payload(payload: dict[str, Any]) -> list[SubtitleCue]:
    source_path = str(payload["source"]["locator"])
    rejoined = payload.get("rejoined") or {}
    segments = rejoined.get("segments") or []
    if segments:
        cues: list[SubtitleCue] = []
        for segment in segments:
            text = _clean_srt_text(segment.get("text", ""))
            if text and segment.get("end_s", 0.0) > segment.get("start_s", 0.0):
                cues.append(
                    SubtitleCue(
                        source_path=source_path,
                        index=len(cues) + 1,
                        start_s=segment["start_s"],
                        end_s=segment["end_s"],
                        text=text,
                    )
                )
        return cues

    cues: list[SubtitleCue] = []
    for transcript in payload.get("transcripts", []):
        text = _clean_srt_text(transcript.get("text", ""))
        chunk = transcript.get("chunk") or {}
        if text and chunk.get("end_s", 0.0) > chunk.get("start_s", 0.0):
            cues.append(
                SubtitleCue(
                    source_path=source_path,
                    index=len(cues) + 1,
                    start_s=chunk["start_s"],
                    end_s=chunk["end_s"],
                    text=text,
                )
            )
    return cues


def _clean_srt_text(text: str) -> str:
    return "\n".join(line.strip() for line in text.strip().splitlines() if line.strip())


def _asr_transcript_text(payload: dict[str, Any]) -> str:
    lines = [_asr_startup_text(payload), ""]
    for index, transcript in enumerate(payload["transcripts"], start=1):
        chunk = transcript["chunk"]
        lines.append(
            f"--- transcript {index}: {chunk['start_s']:.3f}s-{chunk['end_s']:.3f}s ---"
        )
        lines.append(transcript["text"])
        usage = transcript["metadata"].get("usage")
        if usage:
            lines.append("usage=" + json.dumps(usage, sort_keys=True))
        elapsed_s = transcript["metadata"].get("elapsed_s")
        if elapsed_s is not None:
            lines.append(f"elapsed_seconds={elapsed_s:.2f}")
    rejoined = payload.get("rejoined")
    if rejoined:
        lines.extend(["", "--- rejoined ---", rejoined["text"]])
    srt_path = payload.get("srt_path")
    if srt_path:
        lines.extend(["", f"srt: {srt_path}"])
    return "\n".join(lines)


def _rejoin_transcripts(transcripts: list[dict[str, Any]]) -> dict[str, Any]:
    segments = []
    for transcript in transcripts:
        segments.extend(transcript.get("segments") or [])
    if segments:
        segments.sort(key=lambda item: (item["start_s"], item["end_s"]))
        return {
            "strategy": "source_relative_segments",
            "text": " ".join(
                item["text"].strip() for item in segments if item["text"].strip()
            ),
            "segments": segments,
        }

    return {
        "strategy": "chunk_text_order",
        "text": "\n".join(
            transcript["text"].strip()
            for transcript in transcripts
            if transcript.get("text", "").strip()
        ),
        "segments": [],
    }


if __name__ == "__main__":
    main()
