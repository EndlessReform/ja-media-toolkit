from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import time
from typing import Any

import httpx
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt
from tenacity.wait import wait_random_exponential


RETRYABLE_STATUS_CODES = {408, 409, 425, 429}
FALLBACK_WAIT = wait_random_exponential(multiplier=1, max=30)


class RetryableProviderStatus(Exception):
    """An HTTP response that is safe to retry without changing the request."""

    def __init__(self, response: httpx.Response) -> None:
        super().__init__(f"provider returned HTTP {response.status_code}")
        self.response = response


def post_chat_completion(
    client: httpx.Client,
    *,
    custom_id: str,
    provider_name: str,
    base_url: str,
    body: dict[str, Any],
    max_attempts: int = 8,
    sleep: Any = time.sleep,
) -> dict[str, Any]:
    """Post one completion, retrying transient transport and HTTP failures."""

    endpoint = base_url
    if not endpoint.endswith("/chat/completions"):
        endpoint += "/chat/completions"
    request_attempts = 0

    def send() -> httpx.Response:
        nonlocal request_attempts
        request_attempts += 1
        response = client.post(endpoint, json=body)
        if (
            response.status_code in RETRYABLE_STATUS_CODES
            or response.status_code >= 500
        ):
            raise RetryableProviderStatus(response)
        return response

    try:
        response = Retrying(
            retry=retry_if_exception_type(
                (httpx.TransportError, RetryableProviderStatus)
            ),
            stop=stop_after_attempt(max_attempts),
            wait=retry_wait,
            sleep=sleep,
            reraise=True,
        )(send)
    except RetryableProviderStatus as exc:
        response = exc.response
    except httpx.TransportError as exc:
        return {
            "custom_id": custom_id,
            "provider": provider_name,
            "request_attempts": request_attempts,
            "error": {
                "error_kind": "provider_request_error",
                "message": str(exc),
                "retryable": True,
            },
        }
    try:
        payload: Any = response.json()
    except ValueError:
        payload = {"message": response.text}
    return {
        "custom_id": custom_id,
        "provider": provider_name,
        "request_attempts": request_attempts,
        "response": {"status_code": response.status_code, "body": payload},
    }


def retry_wait(retry_state: Any) -> float:
    """Honor Retry-After when supplied; otherwise use jittered backoff."""

    exception = retry_state.outcome.exception() if retry_state.outcome else None
    if isinstance(exception, RetryableProviderStatus):
        retry_after = parse_retry_after(exception.response.headers.get("retry-after"))
        if retry_after is not None:
            return min(retry_after, 120.0)
    return float(FALLBACK_WAIT(retry_state))


def parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            target = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)
        return max(0.0, (target - datetime.now(timezone.utc)).total_seconds())
