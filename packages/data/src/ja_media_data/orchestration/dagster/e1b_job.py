"""Distributed E1-B proof job: server validation, native VAD, server verify."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

import dagster as dg
from dagster_celery import celery_executor

from ja_media_data.orchestration.dagster.e1b_storage import (
    CODE_VERSION,
    MODEL_ID,
    RECIPE_VERSION,
    SELECTION_KEY,
    SpikeStore,
    output_fingerprint,
    output_manifest_key,
    selection_entries,
)


SERVER_QUEUE = {"dagster-celery/queue": "server"}
APPLE_QUEUE = {"dagster-celery/queue": "apple-vad"}
_EXECUTOR = celery_executor.configured(
    {
        "broker": os.environ.get(
            "JA_MEDIA_CELERY_BROKER_URL", "pyamqp://guest@127.0.0.1:5672//"
        ),
        "backend": "rpc://",
        "config_source": {"worker_prefetch_multiplier": 1},
    },
    name="e1b_celery",
)


@dg.op(out=dg.Out(dg.Nothing), tags=SERVER_QUEUE)
def validate_canonical_slice(context) -> None:
    """Confirm the frozen manifest and every declared local source object."""

    store = SpikeStore()
    selection = store.read_json(SELECTION_KEY)
    entries = selection_entries(selection)
    total_bytes = 0
    for entry in entries:
        head = store.head(_required_text(entry, "local_key"))
        expected = _required_int(entry, "bytes")
        if head["bytes"] != expected:
            raise RuntimeError(
                f"source size changed for {entry['locator']}: "
                f"expected {expected}, found {head['bytes']}"
            )
        total_bytes += expected
    context.log.info(
        "Validated %d frozen canonical episodes (%d bytes, fingerprint %s)",
        len(entries),
        total_bytes,
        _required_text(selection, "selection_fingerprint")[:12],
    )


@dg.op(
    ins={"validated": dg.In(dg.Nothing)},
    out=dg.Out(dg.Nothing),
    tags=APPLE_QUEUE,
)
def apple_vad_segments(context) -> None:
    """Run real MLX VAD over the complete frozen slice on a native Mac worker."""

    claimed_at = time.time()
    store = SpikeStore()
    selection = store.read_json(SELECTION_KEY)
    entries = selection_entries(selection)
    selection_fp = _required_text(selection, "selection_fingerprint")
    manifest_key = output_manifest_key(selection_fp)
    try:
        current = store.read_json(manifest_key)
    except store.client.exceptions.NoSuchKey:
        current = None
    except Exception as error:
        response = getattr(error, "response", {})
        if response.get("Error", {}).get("Code") not in {"NoSuchKey", "404"}:
            raise
        current = None
    if current and current.get("output_fingerprint") == output_fingerprint(
        selection_fp
    ):
        context.log.info("Reusing committed VAD product %s", manifest_key)
        _log_materialization(
            context, "e1b_vad_segments", current, manifest_key, "reused"
        )
        return

    context.log.info(
        "Apple worker claimed %d episodes at %.3f", len(entries), claimed_at
    )
    output_entries: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="ja-media-e1b-worker-") as directory:
        work = Path(directory)
        for entry in entries:
            output_entries.append(_process_episode(context, store, work, entry))
    fingerprint = output_fingerprint(selection_fp)
    store.put_json(
        manifest_key,
        {
            "schema_version": 1,
            "selection_fingerprint": selection_fp,
            "output_fingerprint": fingerprint,
            "recipe_version": RECIPE_VERSION,
            "model_id": MODEL_ID,
            "code_version": CODE_VERSION,
            "episodes": output_entries,
        },
    )
    context.log.info(
        "Committed VAD product %s with %d episodes", fingerprint[:12], len(entries)
    )
    _log_materialization(
        context,
        "e1b_vad_segments",
        {"selection_fingerprint": selection_fp, "output_fingerprint": fingerprint},
        manifest_key,
        "committed",
    )


@dg.op(ins={"segmented": dg.In(dg.Nothing)}, tags=SERVER_QUEUE)
def verify_vad_segments(context) -> None:
    """HEAD every declared FLAC after the complete native batch commits."""

    store = SpikeStore()
    selection = store.read_json(SELECTION_KEY)
    selection_fp = _required_text(selection, "selection_fingerprint")
    product = store.read_json(output_manifest_key(selection_fp))
    if product.get("output_fingerprint") != output_fingerprint(selection_fp):
        raise RuntimeError("VAD product fingerprint does not match frozen inputs")
    episodes = product.get("episodes")
    if not isinstance(episodes, list) or len(episodes) != len(selection_entries(selection)):
        raise RuntimeError("VAD product does not cover the frozen slice")
    chunks = 0
    total_bytes = 0
    for episode in episodes:
        if not isinstance(episode, dict) or not isinstance(episode.get("chunks"), list):
            raise RuntimeError("invalid VAD episode manifest")
        for chunk in episode["chunks"]:
            if not isinstance(chunk, dict):
                raise RuntimeError("invalid VAD chunk manifest")
            head = store.head(_required_text(chunk, "key"))
            if head["bytes"] != _required_int(chunk, "bytes"):
                raise RuntimeError(f"VAD chunk changed: {chunk.get('key')}")
            total_bytes += int(head["bytes"])
            chunks += 1
    if chunks < len(episodes):
        raise RuntimeError("each episode must produce at least one FLAC chunk")
    context.log.info(
        "Verified %d episode manifests, %d FLAC objects, %d bytes",
        len(episodes),
        chunks,
        total_bytes,
    )
    _log_materialization(
        context, "e1b_vad_verification", product,
        output_manifest_key(selection_fp), "verified",
        extra={"flac_objects": chunks, "bytes": total_bytes},
    )


@dg.job(executor_def=_EXECUTOR, tags={"ja_media/spike": "phase-e1b"})
def e1b_delayed_vad() -> None:
    """Prove that native-only work can wait and resume across a worker gap."""

    verify_vad_segments(apple_vad_segments(validate_canonical_slice()))


def _process_episode(
    context: object,
    store: SpikeStore,
    work: Path,
    entry: dict[str, object],
) -> dict[str, object]:
    locator = _required_text(entry, "locator")
    episode_dir = work / locator.replace(":", "-")
    input_path = episode_dir / Path(_required_text(entry, "audio_name")).name
    chunks_dir = episode_dir / "chunks"
    chunks_dir.mkdir(parents=True)
    store.download_file(_required_text(entry, "local_key"), input_path)
    command = [
        "uv", "run", "--directory", str(_repo_root() / "envs" / "apple"),
        "ja-media", "vad-local", str(input_path),
        "--model-id", MODEL_ID,
        "--split-every-minutes", "10", "--split-radius-s", "60",
        "--dump-speech-dir", str(chunks_dir), "--dump-audio-format", "flac",
        "--format", "json",
    ]
    started = time.perf_counter()
    result = subprocess.run(command, check=True, text=True, capture_output=True)
    json.loads(result.stdout)
    chunk_paths = sorted(chunks_dir.glob("*.flac"))
    if not chunk_paths:
        raise RuntimeError(f"VAD produced no FLAC chunks for {locator}")
    prefix = output_manifest_key(
        _required_text(store.read_json(SELECTION_KEY), "selection_fingerprint")
    ).removesuffix("manifest.json")
    chunks = [
        store.upload_file(f"{prefix}{locator.replace(':', '-')}/{path.name}", path)
        for path in chunk_paths
    ]
    elapsed = round(time.perf_counter() - started, 3)
    context.log.info("VAD %s produced %d chunks in %.3fs", locator, len(chunks), elapsed)
    return {"locator": locator, "elapsed_seconds": elapsed, "chunks": chunks}


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "envs" / "apple" / "pyproject.toml").is_file():
            return parent
    raise RuntimeError("native Apple runtime is not available in this checkout")


def _log_materialization(
    context: object,
    asset_key: str,
    product: dict[str, object],
    manifest_key: str,
    disposition: str,
    *,
    extra: dict[str, object] | None = None,
) -> None:
    metadata = {
        "selection_fingerprint": str(product["selection_fingerprint"]),
        "output_fingerprint": str(product["output_fingerprint"]),
        "manifest_key": manifest_key,
        "write_disposition": disposition,
    } | (extra or {})
    context.log_event(dg.AssetMaterialization(asset_key=asset_key, metadata=metadata))


def _required_text(value: dict[str, object], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise ValueError(f"{key} must be non-empty text")
    return item


def _required_int(value: dict[str, object], key: str) -> int:
    item = value.get(key)
    if isinstance(item, bool) or not isinstance(item, int) or item < 1:
        raise ValueError(f"{key} must be a positive integer")
    return item
