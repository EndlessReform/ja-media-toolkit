from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from ja_media_frontend.srt_cleaning.contracts import CleanDecision, CleanWindowResult


RETRYABLE_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}
AUTH_STATUS_CODES = {401, 407}


@dataclass(frozen=True)
class WindowResult:
    custom_id: str
    decisions: tuple[CleanDecision, ...]


def parse_batch_result_row(
    row: dict[str, Any],
    *,
    manifests: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    custom_id = str(row.get("custom_id", ""))
    manifest = manifests.get(custom_id)
    if manifest is None:
        return {
            "error": {
                "custom_id": custom_id,
                "error_kind": "unknown_custom_id",
                "message": "No manifest row matches this batch result.",
                "retryable": False,
            }
        }

    if row.get("error") is not None:
        return {"error": api_error(custom_id, manifest, row["error"], None)}

    response = row.get("response")
    if not isinstance(response, dict):
        return {
            "error": base_window_error(
                custom_id,
                "missing_response",
                "Batch row has no response object.",
                manifest,
            )
        }

    status_code = int(response.get("status_code") or 0)
    body = response.get("body")
    if status_code < 200 or status_code >= 300:
        return {"error": api_error(custom_id, manifest, body, status_code)}

    try:
        content = extract_message_content(body)
        result = CleanWindowResult.model_validate_json(content)
    except (KeyError, TypeError, ValidationError, json.JSONDecodeError) as exc:
        error = base_window_error(custom_id, "schema_error", str(exc), manifest)
        error["response_body"] = body
        return {"error": error}

    return {
        "result": WindowResult(
            custom_id=custom_id,
            decisions=tuple(result.decisions),
        )
    }


def extract_message_content(body: Any) -> str:
    if not isinstance(body, dict):
        raise TypeError("response body is not an object")
    choices = body["choices"]
    if not isinstance(choices, list) or not choices:
        raise TypeError("response body has no choices")
    message = choices[0]["message"]
    if not isinstance(message, dict):
        raise TypeError("choice message is not an object")
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    raise TypeError("choice message content is not text")


def api_error(
    custom_id: str,
    manifest: dict[str, Any],
    payload: Any,
    status_code: int | None,
) -> dict[str, Any]:
    retryable = status_code in RETRYABLE_STATUS_CODES if status_code else True
    kind = "api_error"
    if status_code in AUTH_STATUS_CODES:
        kind = "auth_error"
    message = extract_error_message(payload)
    error = base_window_error(custom_id, kind, message, manifest)
    error["status_code"] = status_code
    error["retryable"] = retryable
    error["response_body"] = payload
    return error


def extract_error_message(payload: Any) -> str:
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
        if payload.get("message"):
            return str(payload["message"])
    return str(payload)


def base_window_error(
    custom_id: str,
    kind: str,
    message: str,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    return {
        "custom_id": custom_id,
        "error_kind": kind,
        "message": message,
        "retryable": kind in {"api_error", "missing_result"},
        "anilist_id": manifest["anilist_id"],
        "subtitle_id": manifest["subtitle_id"],
        "repo_path": manifest["repo_path"],
        "source_sha256": manifest["source_sha256"],
        "window_number": manifest["window_number"],
        "active_indexes": manifest["active_indexes"],
    }


def to_dlq_row(error: dict[str, Any], manifest: dict[str, Any] | None) -> dict[str, Any]:
    row = dict(error)
    row["dlq_reason"] = error.get("error_kind", "unknown")
    row["retryable"] = bool(error.get("retryable", False))
    if manifest is not None:
        row["manifest"] = manifest
    return row

