from __future__ import annotations

import argparse
import asyncio
import glob
import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.logging import RichHandler

from ja_media_apple.asr_config import (
    build_selected_asr_backend,
    load_apple_asr_config,
)
from ja_media_apple.vad import DEFAULT_MLX_AUDIO_VAD_MODEL, MlxAudioVadBackend
from ja_media_core.asr import AsrRuntimeOptions, asr_request_from_chunks
from ja_media_core.audio import (
    AudioChunk,
    full_audio_chunk,
    probe_audio_source,
    resolve_audio_source,
    write_audio_chunk,
)
from ja_media_core.vad import (
    VadOptions,
    plan_vad_splits,
    speech_chunks_from_timeline,
)


_CONSOLE = Console(stderr=True)
_LOG = logging.getLogger("ja_media_apple")


def main() -> None:
    _configure_logging()
    parser = argparse.ArgumentParser(
        prog="ja-media",
        description="Apple-local ja-media utilities",
    )
    subparsers = parser.add_subparsers(dest="command")

    vad_parser = subparsers.add_parser(
        "vad-local",
        help="Run local MLX VAD on a client-local audio file",
    )
    vad_parser.add_argument("input", help="Local audio file path")
    vad_parser.add_argument("--start-s", type=float, default=0.0)
    vad_parser.add_argument("--end-s", type=float)
    vad_parser.add_argument("--threshold", type=float)
    vad_parser.add_argument("--min-speech-s", type=float, default=0.25)
    vad_parser.add_argument("--min-silence-s", type=float, default=0.20)
    vad_parser.add_argument("--speech-pad-s", type=float, default=0.05)
    vad_parser.add_argument("--merge-gap-s", type=float, default=0.10)
    vad_parser.add_argument("--channel", type=int)
    vad_parser.add_argument("--model-id", default=DEFAULT_MLX_AUDIO_VAD_MODEL)
    vad_parser.add_argument(
        "--dump-speech-dir",
        help=(
            "Write output chunks as audio files: detected speech spans in plain "
            "VAD mode, planned split chunks with --split-every-minutes"
        ),
    )
    vad_parser.add_argument(
        "--dump-audio-format",
        choices=("wav", "flac"),
        default="wav",
        help="Audio format for dumped chunks. WAV is the default for macOS playback.",
    )
    vad_parser.add_argument(
        "--split-every-minutes",
        type=float,
        help="Plan cuts near every N minutes using bounded VAD search windows",
    )
    vad_parser.add_argument(
        "--split-radius-s",
        type=float,
        default=60.0,
        help="Seconds to inspect on each side of each split target",
    )
    vad_parser.add_argument(
        "--prefer-before-target",
        action="store_true",
        help="Prefer silence before the target when cut candidates tie",
    )
    vad_parser.add_argument(
        "--format",
        choices=("json", "text"),
        default="json",
    )

    transcribe_parser = subparsers.add_parser(
        "transcribe",
        help="Run the configured Apple ASR backend on a client-local audio file",
    )
    transcribe_parser.add_argument(
        "input",
        nargs="+",
        help="Local audio file path or glob pattern. Quote globs to let ja-media expand them.",
    )
    transcribe_parser.add_argument(
        "-c",
        "--config",
        help="Path to ja-media-toolkit TOML config. Defaults to JA_MEDIA_CONFIG or XDG config.",
    )
    transcribe_parser.add_argument(
        "--backend",
        help="Configured ASR backend name. Defaults to [asr].default_backend.",
    )
    transcribe_parser.add_argument("--start-s", type=float, default=0.0)
    transcribe_parser.add_argument("--end-s", type=float)
    transcribe_parser.add_argument("--language", default="ja")
    transcribe_parser.add_argument("--context-info")
    transcribe_parser.add_argument("--hotword", action="append", default=[])
    transcribe_parser.add_argument(
        "--max-concurrent-requests",
        type=int,
        help="Override the selected ASR backend's concurrent vLLM request limit.",
    )
    transcribe_parser.add_argument(
        "--startup-only",
        action="store_true",
        help="Load config/model and print startup metadata without calling vLLM.",
    )
    transcribe_parser.add_argument(
        "--format",
        choices=("json", "text"),
        default="json",
    )
    transcribe_parser.add_argument(
        "--srt-dir",
        help="Write one .srt file per transcribed input into this directory.",
    )

    args = parser.parse_args()
    if args.command == "vad-local":
        run_vad_local(args)
        return
    if args.command == "transcribe":
        run_transcribe(args)
        return

    parser.print_help()


def run_transcribe(args: argparse.Namespace) -> None:
    _LOG.info("[bold]Loading ASR config[/bold]")
    asr_config = load_apple_asr_config(args.config, required=True)
    backend_config = asr_config.get_backend_config(args.backend)
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


def _configure_logging() -> None:
    if logging.getLogger().handlers:
        return
    for logger_name in ("httpx", "httpcore", "huggingface_hub", "urllib3"):
        logging.getLogger(logger_name).setLevel(logging.WARNING)
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        handlers=[
            RichHandler(
                console=_CONSOLE,
                markup=True,
                show_path=False,
                show_time=True,
            )
        ],
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
    chunk = _select_chunk(full_chunk, start_s=args.start_s, end_s=args.end_s)
    asr_chunks = _plan_asr_chunks(chunk, backend_config)
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


def run_vad_local(args: argparse.Namespace) -> None:
    source = resolve_audio_source(args.input, base_dir=Path.cwd(), must_exist=True)
    if source.kind != "client-local":
        raise SystemExit("vad-local only supports client-local files")

    audio_format = probe_audio_source(source)
    full_chunk = full_audio_chunk(
        source,
        audio_format,
        kind="vad_input",
        metadata={"purpose": "local_vad"},
    )
    chunk = _select_chunk(full_chunk, start_s=args.start_s, end_s=args.end_s)
    backend = MlxAudioVadBackend(model_id=args.model_id)
    vad_options = VadOptions(
        threshold=args.threshold,
        min_speech_s=args.min_speech_s,
        min_silence_s=args.min_silence_s,
        speech_pad_s=args.speech_pad_s,
        merge_gap_s=args.merge_gap_s,
        channel=args.channel,
    )

    timeline = None
    speech_chunks = []
    split_chunks = []
    if args.split_every_minutes is not None:
        split_chunks = plan_vad_splits(
            chunk,
            backend=backend,
            every_s=args.split_every_minutes * 60.0,
            search_radius_s=args.split_radius_s,
            vad_options=vad_options,
            prefer_before_target=args.prefer_before_target,
            kind="asr_chunk",
            metadata={"purpose": "periodic_vad_split"},
        )
    else:
        timeline = backend.detect([chunk], options=vad_options)[0]
        speech_chunks = speech_chunks_from_timeline(
            timeline,
            min_duration_s=args.min_speech_s,
            kind="speech",
        )

    dumped_chunk_kind = None
    dumped_chunk_paths = []
    if args.dump_speech_dir is not None:
        dumped_chunks = split_chunks if args.split_every_minutes is not None else speech_chunks
        dumped_chunk_kind = "split" if args.split_every_minutes is not None else "speech"
        dumped_chunk_paths = _dump_audio_chunks(
            dumped_chunks,
            output_dir=Path(args.dump_speech_dir),
            source_id=source.id,
            label=dumped_chunk_kind,
            audio_format=args.dump_audio_format,
        )

    payload = {
        "source": asdict(source),
        "format": asdict(audio_format),
        "chunk": asdict(chunk),
        "backend": backend.name,
        "metadata": {"model_id": backend.model_id} if timeline is None else timeline.metadata,
        "speech_detected": timeline is not None,
        "speech": [] if timeline is None else [asdict(span) for span in timeline.speech],
        "speech_chunks": [asdict(speech_chunk) for speech_chunk in speech_chunks],
        "dumped_chunk_kind": dumped_chunk_kind,
        "dumped_chunk_paths": [str(path) for path in dumped_chunk_paths],
        "split_chunks": [asdict(split_chunk) for split_chunk in split_chunks],
    }

    if args.format == "json":
        print(json.dumps(payload, indent=2, sort_keys=True))
        return

    print(_timeline_text(payload))


def _plan_asr_chunks(
    chunk: AudioChunk,
    backend_config: Any,
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
        vad_options=VadOptions(),
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


def _dump_audio_chunks(
    chunks: list[AudioChunk],
    *,
    output_dir: Path,
    source_id: str,
    label: str,
    audio_format: str,
) -> list[Path]:
    if audio_format not in {"wav", "flac"}:
        raise ValueError(f"Unsupported dump audio format: {audio_format!r}")

    output_paths: list[Path] = []
    for index, chunk in enumerate(chunks, start=1):
        start_ms = round(chunk.start_s * 1000)
        end_ms = round(chunk.end_s * 1000)
        duration_ms = round(chunk.duration_s * 1000)
        output_path = output_dir / (
            f"{source_id}_{label}_{index:03d}_"
            f"src_{start_ms:09d}ms-{end_ms:09d}ms_"
            f"dur_{duration_ms:09d}ms.{audio_format}"
        )
        output_paths.append(
            write_audio_chunk(chunk, output_path, format=audio_format.upper())
        )
    return output_paths


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


def _select_chunk(
    full_chunk: AudioChunk,
    *,
    start_s: float,
    end_s: float | None,
) -> AudioChunk:
    if full_chunk.format is None or full_chunk.format.duration_s is None:
        raise ValueError("Cannot select a local VAD chunk without known duration")
    selected_end_s = full_chunk.format.duration_s if end_s is None else end_s
    if start_s < 0:
        raise ValueError("VAD start must be non-negative")
    if selected_end_s <= start_s:
        raise ValueError("VAD end must be after start")
    if selected_end_s > full_chunk.format.duration_s:
        raise ValueError("VAD end is beyond the source duration")

    sample_rate_hz = full_chunk.format.sample_rate_hz
    return AudioChunk(
        source=full_chunk.source,
        start_s=start_s,
        end_s=selected_end_s,
        source_start_frame=round(start_s * sample_rate_hz),
        source_end_frame=round(selected_end_s * sample_rate_hz),
        format=full_chunk.format,
        kind=full_chunk.kind,
        metadata=dict(full_chunk.metadata),
    )


def _timeline_text(payload: dict[str, Any]) -> str:
    lines = [
        f"source: {payload['source']['locator']}",
        f"model: {payload['metadata'].get('model_id', 'not loaded')}",
        f"chunk: {payload['chunk']['start_s']:.3f}s-{payload['chunk']['end_s']:.3f}s",
    ]
    if payload["speech_detected"]:
        lines.append(f"speech spans: {len(payload['speech'])}")
        for index, span in enumerate(payload["speech"], start=1):
            lines.append(f"{index:>3}. {span['start_s']:.3f}s-{span['end_s']:.3f}s")
    if payload["dumped_chunk_paths"]:
        kind = payload["dumped_chunk_kind"] or "audio"
        lines.append(f"dumped {kind} chunks:")
        for path in payload["dumped_chunk_paths"]:
            lines.append(f"  {path}")
    if payload["split_chunks"]:
        lines.append("split chunks:")
        for item in payload["split_chunks"]:
            metadata = item["metadata"]
            lines.append(
                f"  {item['start_s']:.3f}s-{item['end_s']:.3f}s "
                f"next_target={metadata.get('next_target_s')} "
                f"fallback={metadata.get('next_cut_fallback')} "
                f"reason={metadata.get('next_cut_reason')}"
            )
    return "\n".join(lines)


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
    cues = _srt_cues_from_payload(payload)
    return "\n\n".join(
        "\n".join(
            [
                str(index),
                f"{_srt_timestamp(cue['start_s'])} --> {_srt_timestamp(cue['end_s'])}",
                cue["text"],
            ]
        )
        for index, cue in enumerate(cues, start=1)
    ) + ("\n" if cues else "")


def _srt_cues_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rejoined = payload.get("rejoined") or {}
    segments = rejoined.get("segments") or []
    if segments:
        return [
            {
                "start_s": segment["start_s"],
                "end_s": segment["end_s"],
                "text": _clean_srt_text(segment.get("text", "")),
            }
            for segment in segments
            if segment.get("text", "").strip()
            and segment.get("end_s", 0.0) > segment.get("start_s", 0.0)
        ]

    cues = []
    for transcript in payload.get("transcripts", []):
        text = _clean_srt_text(transcript.get("text", ""))
        chunk = transcript.get("chunk") or {}
        if text and chunk.get("end_s", 0.0) > chunk.get("start_s", 0.0):
            cues.append(
                {
                    "start_s": chunk["start_s"],
                    "end_s": chunk["end_s"],
                    "text": text,
                }
            )
    return cues


def _clean_srt_text(text: str) -> str:
    return "\n".join(line.strip() for line in text.strip().splitlines() if line.strip())


def _srt_timestamp(seconds: float) -> str:
    milliseconds_total = max(0, round(seconds * 1000))
    hours, remainder = divmod(milliseconds_total, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d},{milliseconds:03d}"


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
