from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from ja_media_core.http import ServiceHttpClient


def test_service_http_client_disables_environment_configuration() -> None:
    response = httpx.Response(
        200,
        json={"status": "ok"},
        request=httpx.Request("GET", "http://service.test/healthz"),
    )
    context = MagicMock()
    context.__enter__.return_value.request.return_value = response
    client = ServiceHttpClient(
        "http://service.test",
        timeout_s=5,
        error_label="Service request failed",
    )

    with patch("ja_media_core.http.httpx.Client", return_value=context) as factory:
        payload = client.get_json("/healthz")

    assert payload == {"status": "ok"}
    factory.assert_called_once_with(
        timeout=5,
        trust_env=False,
        follow_redirects=True,
    )
    context.__enter__.return_value.request.assert_called_once_with(
        "GET",
        "http://service.test/healthz",
        headers={"Accept": "application/json"},
    )


def test_service_http_client_preserves_service_error_context() -> None:
    response = httpx.Response(
        503,
        text="not ready",
        request=httpx.Request("GET", "http://service.test/healthz"),
    )
    context = MagicMock()
    context.__enter__.return_value.request.return_value = response
    client = ServiceHttpClient(
        "http://service.test",
        timeout_s=5,
        error_label="Service request failed",
        include_url_in_errors=True,
    )

    with (
        patch("ja_media_core.http.httpx.Client", return_value=context),
        pytest.raises(
            RuntimeError,
            match=(
                "Service request failed for http://service.test/healthz: "
                "503 not ready"
            ),
        ),
    ):
        client.get_json("/healthz")
