"""Import-boundary tests for the lightweight core installation shape."""

from __future__ import annotations

import subprocess
import sys

from ja_media_core.audio import AudioChunk as LegacyAudioChunk
from ja_media_core.audio_contracts import AudioChunk


def test_audio_module_preserves_contract_type_identity() -> None:
    """Existing audio imports resolve to the extracted durable DTO."""

    assert LegacyAudioChunk is AudioChunk


def test_package_import_does_not_load_optional_or_unrelated_modules() -> None:
    """Importing the facade alone stays independent of optional audio I/O."""

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import ja_media_core; "
                "blocked = {'numpy', 'soundfile', 'httpx', 'pysrt', 'ftlangdetect'}; "
                "loaded = blocked.intersection(sys.modules); "
                "assert not loaded, sorted(loaded)"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert completed.stderr == ""


def test_flat_contract_export_is_lazy_and_audio_free() -> None:
    """The compatibility facade resolves pure DTOs without sample libraries."""

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; from ja_media_core import AudioChunk; "
                "assert AudioChunk.__module__ == 'ja_media_core.audio_contracts'; "
                "assert 'numpy' not in sys.modules; "
                "assert 'soundfile' not in sys.modules"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert completed.stderr == ""
