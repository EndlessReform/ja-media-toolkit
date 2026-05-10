from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ja_media_apple.vad import DEFAULT_MLX_AUDIO_VAD_MODEL, MlxAudioVadBackend
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


def main() -> None:
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
        help="Write detected speech spans as individual FLAC files",
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

    args = parser.parse_args()
    if args.command == "vad-local":
        run_vad_local(args)
        return

    parser.print_help()


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
    if args.split_every_minutes is None or args.dump_speech_dir is not None:
        timeline = backend.detect([chunk], options=vad_options)[0]
        speech_chunks = speech_chunks_from_timeline(
            timeline,
            min_duration_s=args.min_speech_s,
            kind="speech",
        )

    dumped_speech_paths = []
    if args.dump_speech_dir is not None:
        dumped_speech_paths = _dump_speech_chunks(
            speech_chunks,
            output_dir=Path(args.dump_speech_dir),
            source_id=source.id,
        )

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

    payload = {
        "source": asdict(source),
        "format": asdict(audio_format),
        "chunk": asdict(chunk),
        "backend": backend.name,
        "metadata": {"model_id": backend.model_id} if timeline is None else timeline.metadata,
        "speech_detected": timeline is not None,
        "speech": [] if timeline is None else [asdict(span) for span in timeline.speech],
        "speech_chunks": [asdict(speech_chunk) for speech_chunk in speech_chunks],
        "dumped_speech_paths": [str(path) for path in dumped_speech_paths],
        "split_chunks": [asdict(split_chunk) for split_chunk in split_chunks],
    }

    if args.format == "json":
        print(json.dumps(payload, indent=2, sort_keys=True))
        return

    print(_timeline_text(payload))


def _dump_speech_chunks(
    chunks: list[AudioChunk],
    *,
    output_dir: Path,
    source_id: str,
) -> list[Path]:
    output_paths: list[Path] = []
    for index, chunk in enumerate(chunks, start=1):
        output_path = output_dir / (
            f"{source_id}_speech_{index:03d}_"
            f"{round(chunk.start_s * 1000):09d}ms-"
            f"{round(chunk.end_s * 1000):09d}ms.flac"
        )
        output_paths.append(write_audio_chunk(chunk, output_path, format="FLAC"))
    return output_paths


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
    if payload["dumped_speech_paths"]:
        lines.append("dumped speech:")
        for path in payload["dumped_speech_paths"]:
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


if __name__ == "__main__":
    main()
