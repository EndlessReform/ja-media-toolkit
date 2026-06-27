"""Reusable local-audio materialization and playback primitives.

Interactive review surfaces need low-latency, repeatable playback of many tiny
ranges.  Re-seeking a compressed MKV for every range is especially unpleasant
when the source lives on NFS-backed spinning storage, so this module pays for
one sequential ffmpeg decode and retains compact mono ``int16`` PCM in memory.

Playback itself deliberately uses sounddevice's convenience API.  It already
owns the PortAudio callback and interruption lifecycle; callers should not
grow their own output threads merely to stop one range and start another.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np
import sounddevice
from numpy.typing import NDArray

from ja_media_core.proc import run as run_process


DEFAULT_PLAYBACK_SAMPLE_RATE = 48_000
DEFAULT_PLAYBACK_CHANNELS = 1


class PlaybackBackend(Protocol):
    """Small seam around sounddevice used by the player and its tests."""

    def play(
        self,
        data: NDArray[np.int16],
        samplerate: int,
        *,
        blocking: bool = False,
    ) -> object: ...

    def stop(self, *, ignore_errors: bool = True) -> object: ...

    def get_stream(self) -> object: ...


@dataclass(frozen=True)
class MaterializedAudio:
    """A fully decoded PCM source suitable for cheap frame-range slicing."""

    source_path: Path
    sample_rate: int
    samples: NDArray[np.int16]

    @property
    def channels(self) -> int:
        return int(self.samples.shape[1])

    @property
    def frame_count(self) -> int:
        return int(self.samples.shape[0])

    @property
    def duration_s(self) -> float:
        return self.frame_count / self.sample_rate

    def slice_samples(
        self,
        start_s: float,
        duration_s: float,
    ) -> NDArray[np.int16]:
        """Return a zero-copy view over the requested frame range."""

        start_frame = max(0, round(start_s * self.sample_rate))
        requested_frames = max(1, round(duration_s * self.sample_rate))
        end_frame = min(self.frame_count, start_frame + requested_frames)
        return self.samples[start_frame:end_frame]


def materialize_audio(
    source: Path,
    *,
    sample_rate: int = DEFAULT_PLAYBACK_SAMPLE_RATE,
    channels: int = DEFAULT_PLAYBACK_CHANNELS,
) -> MaterializedAudio:
    """Decode the first audio stream once into compact signed 16-bit PCM.

    The decode intentionally reads the complete source sequentially.  A
    24-minute mono 48 kHz source occupies about 138 MB, avoiding repeated
    container seeks, decoder preroll, and network reads during review.

    PCM is written to a temporary file rather than piped through stdout.  On
    macOS, a prior HTTP request (e.g. derived-audio cache validation) can
    initialize Network.framework globals that make fork-based subprocesses
    unstable when large pipe buffers are involved, causing intermittent
    SIGSEGVs in ffmpeg.  A file destination keeps the subprocess simple and
    sidesteps that platform bug.
    """

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("ffmpeg not found; cannot decode source audio")

    # PCM streams out over stdout and diagnostics over stderr.  This is the
    # straightforward, fast path; the macOS fork()-after-HTTP SIGSEGV that used
    # to make piped subprocesses unstable is handled centrally by
    # ``ja_media_core.proc.run`` (it forces posix_spawn on Darwin).  See that
    # module and ``docs/macos-fork-subprocess.md`` for the full story.
    result = run_process(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-map",
            "0:a:0",
            "-vn",
            "-ac",
            str(channels),
            "-ar",
            str(sample_rate),
            "-c:a",
            "pcm_s16le",
            "-f",
            "s16le",
            "pipe:1",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        detail = detail or "ffmpeg produced no diagnostic output"
        raise RuntimeError(
            f"Could not decode first audio stream with ffmpeg:\n{detail}"
        )

    samples = np.frombuffer(result.stdout, dtype="<i2")
    if samples.size == 0:
        raise RuntimeError(f"ffmpeg decoded no audio from source: {source}")
    if samples.size % channels != 0:
        raise RuntimeError("Decoded PCM sample count is not divisible by channels")
    return MaterializedAudio(
        source_path=source,
        sample_rate=sample_rate,
        samples=samples.reshape((-1, channels)),
    )


class MaterializedAudioPlayer:
    """Play interruptible ranges from a fully materialized audio source."""

    def __init__(
        self,
        audio: MaterializedAudio,
        *,
        backend: PlaybackBackend = sounddevice,
    ) -> None:
        self.audio = audio
        self._backend = backend
        self._started = False

    def play(self, start_s: float, duration_s: float) -> None:
        """Interrupt current playback and asynchronously play one range."""

        samples = self.audio.slice_samples(start_s, duration_s)
        if not samples.size:
            self.stop()
            return
        self._backend.play(samples, self.audio.sample_rate, blocking=False)
        self._started = True

    def stop(self) -> None:
        """Stop convenience playback, if this player has started it."""

        if self._started:
            self._backend.stop()
            self._started = False

    def is_playing(self) -> bool:
        """Return whether sounddevice's current convenience stream is active."""

        if not self._started:
            return False
        try:
            return bool(self._backend.get_stream().active)
        except RuntimeError:
            return False
