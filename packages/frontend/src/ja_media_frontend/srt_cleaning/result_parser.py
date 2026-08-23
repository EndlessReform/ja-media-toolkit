from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ja_media_frontend.srt_cleaning.contracts import CleanDecision, CleanWindowResult


RETRYABLE_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}
AUTH_STATUS_CODES = {401, 407}


class LegacyCleanDecision(BaseModel):
    """Read-only parser for retained clean:v1 provider results."""

    model_config = ConfigDict(extra="forbid")
    cue_id: int = Field(alias="id")
    decision: Literal["as_is", "asis", "edit", "remove", "escalate"]
    text: str | None = None
    category: str | None = None


class LegacyCleanWindowResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decisions: list[LegacyCleanDecision]


@dataclass(frozen=True)
class WindowResult:
    custom_id: str
    decisions: tuple[CleanDecision, ...]
    served_model: str | None = None


def parse_batch_result_row(
    row: dict[str, Any],
    *,
    manifests: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    row = _recover_validation_response(row)
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
        payload = row["error"]
        if isinstance(payload, dict) and payload.get("error_kind"):
            error = base_window_error(
                custom_id,
                str(payload["error_kind"]),
                str(payload.get("message", payload["error_kind"])),
                manifest,
            )
            error.update(
                {key: value for key, value in payload.items() if key != "error_kind"}
            )
            return {"error": error}
        return {"error": api_error(custom_id, manifest, payload, None)}

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
        result = parse_clean_result(content, manifest)
    except (KeyError, TypeError, ValidationError, json.JSONDecodeError) as exc:
        error = base_window_error(custom_id, "schema_error", str(exc), manifest)
        error["response_body"] = body
        return {"error": error}

    result = normalize_noop_edits(manifest, result)
    validation_errors = validate_window_result(manifest, result)
    if validation_errors:
        error = base_window_error(
            custom_id,
            "decision_validation_error",
            "; ".join(validation_errors),
            manifest,
        )
        error["validation_errors"] = validation_errors
        error["response_body"] = body
        return {"error": error}

    return {
        "result": WindowResult(
            custom_id=custom_id,
            decisions=tuple(result.decisions),
            served_model=(
                str(body["model"])
                if isinstance(body, dict) and body.get("model")
                else None
            ),
        )
    }


def normalize_noop_edits(
    manifest: dict[str, Any], result: CleanWindowResult
) -> CleanWindowResult:
    """Treat an exact no-op edit as acceptance of the supplied baseline."""

    if str(manifest.get("pipeline_version")) == "clean:v1":
        return result
    active_texts = manifest.get("active_texts", [])
    decisions: list[CleanDecision] = []
    for decision in result.decisions:
        baseline = (
            active_texts[decision.cue_id - 1]
            if 0 < decision.cue_id <= len(active_texts)
            else None
        )
        if decision.decision == "edit" and decision.text == baseline:
            decision = decision.model_copy(
                update={"decision": "as_is", "text": None, "reasons": []}
            )
        decisions.append(decision)
    return result.model_copy(update={"decisions": decisions})


def _recover_validation_response(row: dict[str, Any]) -> dict[str, Any]:
    """Expose the final saved response from an exhausted validation retry."""

    error = row.get("error")
    if not isinstance(error, dict) or error.get("error_kind") != (
        "validation_retry_exhausted"
    ):
        return row
    attempts = error.get("attempts")
    if not isinstance(attempts, list):
        return row
    for attempt in reversed(attempts):
        if isinstance(attempt, dict) and isinstance(attempt.get("response"), dict):
            return {**row, "error": None, "response": attempt["response"]}
    return row


def parse_clean_result(content: str, manifest: dict[str, Any]) -> CleanWindowResult:
    if str(manifest.get("pipeline_version")) != "clean:v1":
        return CleanWindowResult.model_validate_json(content)
    legacy = LegacyCleanWindowResult.model_validate_json(content)
    decisions = [
        CleanDecision.model_construct(
            cue_id=decision.cue_id,
            decision="as_is" if decision.decision == "asis" else decision.decision,
            text=decision.text,
            reasons=[decision.category] if decision.category else [],
        )
        for decision in legacy.decisions
    ]
    return CleanWindowResult.model_construct(decisions=decisions)


def validate_window_result(
    manifest: dict[str, Any], result: CleanWindowResult
) -> list[str]:
    """Check cue coverage and reject edits that do not change the baseline."""

    active_texts = manifest.get("active_texts", [])
    expected = set(range(1, len(manifest.get("active_indexes", [])) + 1))
    seen: set[int] = set()
    errors: list[str] = []
    for decision in result.decisions:
        cue_id = decision.cue_id
        if cue_id not in expected:
            errors.append(f"cue id {cue_id} is outside expected ids {sorted(expected)}")
            continue
        if cue_id in seen:
            errors.append(f"cue id {cue_id} appears more than once")
        seen.add(cue_id)
        baseline = active_texts[cue_id - 1] if cue_id <= len(active_texts) else None
        if (
            manifest.get("pipeline_version") != "clean:v1"
            and decision.decision == "edit"
            and decision.text == baseline
        ):
            errors.append(f"cue id {cue_id} is an edit that does not change the text")
    missing = sorted(expected - seen)
    if missing:
        errors.append(f"missing cue ids {missing}")
    return errors


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
        "retryable": kind
        in {
            "api_error",
            "missing_result",
            "schema_error",
            "decision_validation_error",
        },
        "anilist_id": manifest["anilist_id"],
        "subtitle_id": manifest["subtitle_id"],
        "repo_path": manifest["repo_path"],
        "source_sha256": manifest["source_sha256"],
        "window_number": manifest["window_number"],
        "active_indexes": manifest["active_indexes"],
    }


def to_dlq_row(
    error: dict[str, Any], manifest: dict[str, Any] | None
) -> dict[str, Any]:
    row = dict(error)
    row["dlq_reason"] = error.get("error_kind", "unknown")
    row["retryable"] = bool(error.get("retryable", False))
    if manifest is not None:
        row["manifest"] = manifest
    return row
