"""Typed, storage-neutral contracts for committed bronze capture manifests.

Legacy manifests predate explicit schema and series fields.  The parser accepts
their repository key as context so downstream code gets one honest contract
without rewriting immutable bronze evidence.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any


class BronzeManifestError(ValueError):
    """A committed marker cannot be interpreted as a supported manifest."""


@dataclass(frozen=True)
class BronzeSeries:
    """Strong series grouping supplied by ingest or the legacy object layout."""

    namespace: str
    identifier: str


@dataclass(frozen=True)
class BronzeStream:
    """Small stream header retained for resolver evidence and later checks."""

    object_name: str
    stream_index: int
    codec: str | None
    declared_language: str | None
    is_default: bool
    channels: int | None = None
    channel_layout: str | None = None
    sample_rate: int | None = None
    bit_rate: int | None = None


@dataclass(frozen=True)
class BronzeCaptureManifest:
    """Normalized immutable evidence read from one bronze commit marker."""

    schema_version: int
    capture_id: str
    series: BronzeSeries
    source_hint: str
    stem: str
    audio_tracks: tuple[BronzeStream, ...]
    subtitles: tuple[BronzeStream, ...]

    @property
    def audio(self) -> BronzeStream:
        """Return the sole legacy audio track without hiding v2 ambiguity."""

        if len(self.audio_tracks) != 1:
            raise BronzeManifestError(
                "manifest has multiple audio tracks; select one explicitly"
            )
        return self.audio_tracks[0]


def parse_bronze_manifest(
    payload: Mapping[str, Any], *, capture_id: str, manifest_key: str
) -> BronzeCaptureManifest:
    """Normalize one v1/v2 manifest without inventing episode identity."""

    schema_version = _schema_version(payload.get("schema_version", 1))
    series = _series(payload.get("series"), manifest_key)
    source_hint = _source_hint(payload)
    stem = _text(payload.get("stem")) or PurePosixPath(source_hint).stem
    if not stem:
        raise BronzeManifestError("manifest has no usable filename stem")
    audio_tracks = _audio_tracks(payload, schema_version)
    subtitles_value = payload.get("subtitles", ())
    if not isinstance(subtitles_value, list):
        raise BronzeManifestError("subtitles must be a list")
    subtitles = tuple(
        _stream(value, field=f"subtitles[{index}]")
        for index, value in enumerate(subtitles_value)
    )
    return BronzeCaptureManifest(
        schema_version=schema_version,
        capture_id=_required_text(capture_id, "capture_id"),
        series=series,
        source_hint=source_hint,
        stem=stem,
        audio_tracks=audio_tracks,
        subtitles=subtitles,
    )


def _schema_version(value: object) -> int:
    if isinstance(value, bool):
        raise BronzeManifestError("schema_version must be 1 or 2")
    try:
        version = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as error:
        raise BronzeManifestError("schema_version must be 1 or 2") from error
    if version not in {1, 2}:
        raise BronzeManifestError(f"unsupported bronze schema version {version}")
    return version


def _series(value: object, manifest_key: str) -> BronzeSeries:
    if isinstance(value, Mapping):
        namespace = _text(value.get("namespace"))
        identifier = _text(value.get("id"))
        if namespace and identifier:
            return BronzeSeries(namespace=namespace, identifier=identifier)

    parts = PurePosixPath(manifest_key).parts
    try:
        metadata_index = len(parts) - 1 - tuple(reversed(parts)).index("metadata")
        identifier = parts[metadata_index - 1]
    except (ValueError, IndexError) as error:
        raise BronzeManifestError(
            "legacy manifest key cannot supply its AniList series ID"
        ) from error
    return BronzeSeries(namespace="anilist", identifier=identifier)


def _source_hint(payload: Mapping[str, Any]) -> str:
    source_hint = _text(payload.get("source_hint")) or _text(payload.get("source"))
    if source_hint:
        return PurePosixPath(source_hint).name
    audio = payload.get("audio")
    if isinstance(audio, Mapping):
        audio_name = _text(audio.get("key")) or _text(audio.get("filename"))
        if audio_name:
            return PurePosixPath(audio_name).name
    audio_tracks = payload.get("audio_tracks")
    if isinstance(audio_tracks, list) and audio_tracks:
        first = audio_tracks[0]
        if isinstance(first, Mapping):
            audio_name = _text(first.get("key")) or _text(first.get("filename"))
            if audio_name:
                return PurePosixPath(audio_name).name
    raise BronzeManifestError(
        "manifest has no source_hint, source, audio name, or audio_tracks name"
    )


def _audio_tracks(
    payload: Mapping[str, Any], schema_version: int
) -> tuple[BronzeStream, ...]:
    """Normalize legacy singular audio and v2 ordered audio tracks."""

    if schema_version == 1:
        return (_stream(payload.get("audio"), field="audio"),)
    values = payload.get("audio_tracks")
    if not isinstance(values, list):
        raise BronzeManifestError("audio_tracks must be a list")
    if not values:
        raise BronzeManifestError("audio_tracks must contain at least one track")
    return tuple(
        _stream(value, field=f"audio_tracks[{index}]")
        for index, value in enumerate(values)
    )


def _stream(value: object, *, field: str) -> BronzeStream:
    if not isinstance(value, Mapping):
        raise BronzeManifestError(f"{field} must be an object")
    object_name = _text(value.get("key")) or _text(value.get("filename"))
    if not object_name:
        raise BronzeManifestError(f"{field} has no key or filename")
    stream_index = value.get("stream_index")
    if isinstance(stream_index, bool) or not isinstance(stream_index, int):
        raise BronzeManifestError(f"{field}.stream_index must be an integer")
    return BronzeStream(
        object_name=object_name,
        stream_index=stream_index,
        codec=_text(value.get("source_codec")) or _text(value.get("codec")),
        declared_language=_text(value.get("declared_language")),
        is_default=value.get("is_default") is True,
        channels=_optional_int(value.get("channels"), f"{field}.channels"),
        channel_layout=_text(value.get("channel_layout")),
        sample_rate=_optional_int(value.get("sample_rate"), f"{field}.sample_rate"),
        bit_rate=_optional_int(value.get("bit_rate"), f"{field}.bit_rate"),
    )


def _optional_int(value: object, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise BronzeManifestError(f"{field} must be a non-negative integer")
    return value


def _required_text(value: object, field: str) -> str:
    result = _text(value)
    if result is None:
        raise BronzeManifestError(f"{field} must be non-empty text")
    return result


def _text(value: object) -> str | None:
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        return None
    result = str(value).strip()
    return result or None
