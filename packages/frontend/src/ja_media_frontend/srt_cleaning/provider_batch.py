from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from rich.console import Console

from ja_media_frontend.srt_cleaning.batch import encode_jsonl_row, read_jsonl
from ja_media_frontend.srt_cleaning.execution_manifest import write_execution_manifest
from ja_media_frontend.srt_cleaning.provider_http import post_chat_completion
from ja_media_frontend.srt_cleaning.provider_reconstruct import (
    reconstruct_provider_output,
    reusable_result_rows,
)
from ja_media_frontend.srt_cleaning.result_parser import parse_batch_result_row
from ja_media_frontend.srt_cleaning.workspace import find_repo_root, run_for_anilist


console = Console()
error_console = Console(stderr=True)
REPAIRABLE_ERRORS = {"schema_error", "decision_validation_error"}


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    base_url: str
    api_key_env: str
    model: str
    response_format: str


def run_provider_batch(args: argparse.Namespace) -> None:
    """Execute generated requests with one optional stateless repair attempt."""

    input_path, manifest_path, output_path = resolve_paths(args)
    for path in (input_path, manifest_path):
        if not path.is_file():
            fail(f"Input does not exist: {path}")
    if args.resume and args.force:
        fail("Use either --resume or --force, not both.")
    if output_path.exists() and not (args.resume or args.force):
        fail(f"Output already exists; pass --force to replace it: {output_path}")
    if args.concurrency < 1:
        fail("--concurrency must be at least 1")
    if args.request_attempts < 1:
        fail("--request-attempts must be at least 1")

    load_dotenv(find_repo_root(Path.cwd()) / ".env", override=False)
    provider = resolve_provider(args)
    api_key = os.environ.get(provider.api_key_env)
    if not api_key:
        fail(f"Missing API key environment variable: {provider.api_key_env}")
    body_override = parse_body_override(args.body_json)
    manifests = {str(row["custom_id"]): row for row in read_jsonl(manifest_path)}
    requests = read_jsonl(input_path)
    if args.limit is not None:
        if args.limit < 1:
            fail("--limit must be at least 1")
        requests = requests[: args.limit]
    selected_request_ids = {str(row.get("custom_id", "")) for row in requests}

    retained_rows = (
        reusable_result_rows(read_jsonl(output_path), manifests)
        if args.resume and output_path.exists()
        else []
    )
    completed_ids = {str(row.get("custom_id", "")) for row in retained_rows}
    requests = [
        row for row in requests if str(row.get("custom_id", "")) not in completed_ids
    ]
    total = len(requests)
    console.print(
        f"Starting [cyan]{total}[/] request(s); "
        f"[cyan]{len(selected_request_ids & completed_ids)}[/] already complete."
    )

    headers = {"Authorization": f"Bearer {api_key}"}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("wb") as output_handle:
        for row in retained_rows:
            output_handle.write(encode_jsonl_row(row))
        output_handle.flush()
        with httpx.Client(headers=headers, timeout=args.timeout) as client:

            def execute(row: dict[str, Any]) -> dict[str, Any]:
                return execute_request(
                    client,
                    row,
                    manifests=manifests,
                    provider=provider,
                    body_override=body_override,
                    request_attempts=args.request_attempts,
                    repair_attempts=args.repair_attempts,
                )

            with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
                futures = [pool.submit(execute, row) for row in requests]
                for finished, future in enumerate(as_completed(futures), start=1):
                    output_handle.write(encode_jsonl_row(future.result()))
                    output_handle.flush()
                    if finished % 25 == 0 or finished == total:
                        console.print(f"Completed [cyan]{finished}/{total}[/].")

    results = read_jsonl(output_path)
    execution_path = write_execution_manifest(
        output_path,
        provider=provider.name,
        requested_model=provider.model,
        rows=results,
    )
    console.print(f"[green]Provider results:[/] [cyan]{output_path}[/]")
    console.print(f"[green]Execution manifest:[/] [cyan]{execution_path}[/]")
    reconstruct_provider_output(
        manifest_path=manifest_path,
        output_path=output_path,
        request_ids=selected_request_ids,
        limited=args.limit is not None,
        console=console,
    )


def resolve_provider(args: argparse.Namespace) -> ProviderConfig:
    presets = {
        "openai": ProviderConfig(
            "openai",
            "https://api.openai.com/v1",
            "OPENAI_API_KEY",
            "gpt-5.6-luna",
            "keep",
        ),
        "deepseek": ProviderConfig(
            "deepseek",
            "https://api.deepseek.com",
            "DEEPSEEK_API_KEY",
            "deepseek-v4-flash",
            "json-object",
        ),
    }
    preset = presets.get(args.provider)
    if preset is None and not args.base_url:
        fail("--provider custom requires --base-url")
    if preset is None and not args.model:
        fail("--provider custom requires --model")
    response_format = args.response_format
    if response_format == "auto":
        response_format = preset.response_format if preset else "keep"
    return ProviderConfig(
        name=args.provider,
        base_url=(args.base_url or preset.base_url).rstrip("/"),
        api_key_env=args.api_key_env
        or (preset.api_key_env if preset else "OPENAI_API_KEY"),
        model=args.model or preset.model,
        response_format=response_format,
    )


def resolve_paths(args: argparse.Namespace) -> tuple[Path, Path, Path]:
    if args.anilist is not None:
        if args.input or args.manifest:
            fail("Use either --anilist or explicit --input/--manifest paths.")
        root = Path(args.workspace_root).expanduser() if args.workspace_root else None
        run = run_for_anilist(args.anilist, workspace_root=root, run_id=args.run_id)
        return (
            run.run_dir / "batch-00001.jsonl",
            run.manifest_path,
            (Path(args.out).expanduser().resolve() if args.out else run.results_path),
        )
    if not args.input or not args.manifest:
        fail("Provide --anilist or both --input and --manifest.")
    input_path = Path(args.input).expanduser().resolve()
    manifest_path = Path(args.manifest).expanduser().resolve()
    output_path = (
        Path(args.out).expanduser().resolve()
        if args.out
        else input_path.with_name("results.jsonl")
    )
    return input_path, manifest_path, output_path


def execute_request(
    client: httpx.Client,
    request: dict[str, Any],
    *,
    manifests: dict[str, dict[str, Any]],
    provider: ProviderConfig,
    body_override: dict[str, Any],
    request_attempts: int = 8,
    repair_attempts: int,
) -> dict[str, Any]:
    custom_id = str(request.get("custom_id", ""))
    body = prepare_body(request.get("body"), provider, body_override)
    failures: list[dict[str, Any]] = []
    for attempt in range(repair_attempts + 1):
        row = post_chat_completion(
            client,
            custom_id=custom_id,
            provider_name=provider.name,
            base_url=provider.base_url,
            body=body,
            max_attempts=request_attempts,
        )
        parsed = parse_batch_result_row(row, manifests=manifests)
        error = parsed.get("error")
        if error is None:
            if failures:
                row["repair_failures"] = failures
            return row
        if (
            error.get("error_kind") not in REPAIRABLE_ERRORS
            or attempt >= repair_attempts
        ):
            if not failures:
                return row
            failures.append(compact_failure(error, row))
            return {
                "custom_id": custom_id,
                "provider": provider.name,
                "error": {
                    "error_kind": "validation_retry_exhausted",
                    "message": str(
                        error.get("message", "model output remained invalid")
                    ),
                    "retryable": False,
                    "attempts": failures,
                },
            }
        failures.append(compact_failure(error, row))
        body = repair_body(body, error, row)
    raise AssertionError("repair loop exhausted unexpectedly")


def prepare_body(
    raw_body: Any, provider: ProviderConfig, override: dict[str, Any]
) -> dict[str, Any]:
    if not isinstance(raw_body, dict):
        raise ValueError("request body is not an object")
    body = {**raw_body, **override, "model": provider.model}
    if provider.response_format == "json-object":
        body["response_format"] = {"type": "json_object"}
    elif provider.response_format == "none":
        body.pop("response_format", None)
    return body


def repair_body(
    body: dict[str, Any], error: dict[str, Any], row: dict[str, Any]
) -> dict[str, Any]:
    repaired = dict(body)
    messages = list(body.get("messages", []))
    response_body = row.get("response", {}).get("body", {})
    messages.append(
        {
            "role": "user",
            "content": (
                "Your previous JSON failed validation. Return the complete corrected JSON "
                "for the same active cues.\n\nValidation failures:\n"
                f"{error.get('message')}\n\nInvalid response:\n"
                f"{json.dumps(response_body, ensure_ascii=False)}"
            ),
        }
    )
    repaired["messages"] = messages
    return repaired


def compact_failure(error: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    return {
        "error_kind": error.get("error_kind"),
        "message": error.get("message"),
        "response": row.get("response"),
    }


def parse_body_override(value: str | None) -> dict[str, Any]:
    if value is None:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        fail(f"--body-json is invalid JSON: {exc}")
    if not isinstance(parsed, dict):
        fail("--body-json must be a JSON object")
    return parsed


def fail(message: str) -> None:
    error_console.print(f"[bold red]Error:[/] {message}")
    raise SystemExit(2)
