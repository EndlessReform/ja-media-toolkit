from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Sequence

from ja_media_core.audio import (
    AudioChunk,
    full_audio_chunk,
    probe_audio_source,
    resolve_audio_source,
)
from ja_media_core.proc import run as run_process
from ja_media_core.vocal_separation import (
    VocalSeparationOptions,
    VocalSeparationResult,
)


DEFAULT_DEMUCS_MODEL = "htdemucs"


# Demucs on the Apple runtime should default to Metal: there is no CUDA on
# macOS, and Demucs's own auto-selection ("cuda if available else cpu") falls
# back to CPU on Macs even when MPS is present.  This keeps the repo-managed
# dependency honest about where it actually runs.
def _default_device() -> str:
    try:
        import torch
    except ImportError:
        return "cpu"
    try:
        if torch.backends.mps.is_available():
            return "mps"
    except Exception:
        pass
    return "cpu"


class DemucsVocalSeparationBackend:
    """Local Demucs CLI adapter that produces a cacheable vocal stem.

    The backend calls the ``demucs`` console script installed in the Apple uv
    environment.  Keeping that at the process boundary leaves the core primitive
    open to UVR or a remote separator later without changing callers that only
    need a stem ``AudioChunk``.
    """

    name = "demucs"

    def __init__(
        self,
        *,
        model: str = DEFAULT_DEMUCS_MODEL,
        device: str | None = None,
        jobs: int | None = None,
        segment_s: float | None = None,
        command: str = "demucs",
    ) -> None:
        self.model = model
        # Resolve MPS lazily so import-time torch probing is deferred to the
        # moment a separation actually runs; this also keeps the backend cheap
        # to construct in tests that never execute Demucs.
        self.device = device if device is not None else _default_device()
        self.jobs = jobs
        self.segment_s = segment_s
        self.command = command

    def separate(
        self,
        chunks: Sequence[AudioChunk],
        *,
        options: VocalSeparationOptions | None = None,
    ) -> list[VocalSeparationResult]:
        active_options = options or VocalSeparationOptions()
        return [
            self.separate_chunk(chunk, options=active_options)
            for chunk in chunks
        ]

    def separate_chunk(
        self,
        chunk: AudioChunk,
        *,
        options: VocalSeparationOptions | None = None,
    ) -> VocalSeparationResult:
        active_options = options or VocalSeparationOptions()
        if chunk.source.kind != "client-local":
            raise ValueError("Demucs vocal separation requires client-local audio")
        source_path = Path(chunk.source.locator)
        cache_dir = (
            active_options.cache_dir
            or Path.home() / ".cache" / "ja-media-toolkit" / "vocal-separation"
        ).expanduser()
        cache_key = _cache_key(chunk, self, active_options)
        output_root = cache_dir / cache_key
        expected_path = output_root / self.model / source_path.stem / f"{active_options.stem}.wav"
        cache_hit = expected_path.is_file()

        if not cache_hit:
            executable = shutil.which(self.command)
            if executable is None:
                raise RuntimeError(
                    "Demucs is not available on PATH. Run this from the synced "
                    "ja-media-apple environment so its repo-managed demucs "
                    "console script is available."
                )
            output_root.mkdir(parents=True, exist_ok=True)
            run_process(
                _demucs_command(
                    executable,
                    chunk,
                    self,
                    active_options,
                    output_root=output_root,
                ),
                check=True,
            )

        if not expected_path.is_file():
            raise RuntimeError(f"Demucs did not produce expected stem: {expected_path}")

        stem_source = resolve_audio_source(expected_path, must_exist=True)
        stem_format = probe_audio_source(stem_source)
        stem_chunk = full_audio_chunk(
            stem_source,
            stem_format,
            kind="vocal_stem",
            metadata={
                "source_chunk": chunk.source.id,
                "stem": active_options.stem,
                "separator_backend": self.name,
                "separator_model": self.model,
            },
        )
        return VocalSeparationResult(
            source_chunk=chunk,
            stem_chunk=stem_chunk,
            backend=self.name,
            cache_hit=cache_hit,
            metadata={
                **active_options.metadata,
                "model": self.model,
                "stem": active_options.stem,
                "path": str(expected_path),
            },
        )


def _demucs_command(
    executable: str,
    chunk: AudioChunk,
    backend: DemucsVocalSeparationBackend,
    options: VocalSeparationOptions,
    *,
    output_root: Path,
) -> list[str]:
    command = [
        executable,
        "-n",
        backend.model,
        "--two-stems",
        options.stem,
        "-o",
        str(output_root),
    ]
    # ``backend.device`` is always resolved in ``__init__`` (to ``mps`` by
    # default on Apple Silicon, or ``cpu`` when MPS/torch is unavailable), so
    # always pass ``-d`` explicitly rather than rely on Demucs's own
    # "cuda else cpu" auto-selection, which silently picks CPU on a Mac.
    command.extend(["-d", backend.device])
    if backend.jobs is not None:
        command.extend(["-j", str(backend.jobs)])
    if backend.segment_s is not None:
        command.extend(["--segment", f"{backend.segment_s:g}"])
    command.append(chunk.source.locator)
    return command


def _cache_key(
    chunk: AudioChunk,
    backend: DemucsVocalSeparationBackend,
    options: VocalSeparationOptions,
) -> str:
    path = Path(chunk.source.locator)
    stat = path.stat()
    payload = "|".join(
        [
            str(path.resolve()),
            str(stat.st_mtime_ns),
            str(stat.st_size),
            f"{chunk.start_s:.9f}",
            f"{chunk.end_s:.9f}",
            backend.name,
            backend.model,
            options.stem,
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]
