"""Shared HTTPX transport for first-party service SDKs.

LAN clients deliberately ignore ambient proxy configuration. On macOS,
consulting system proxy state can initialize Network.framework globals that
are unsafe in a later fork-based subprocess. These services are reached
directly through the configured gateway, so inheriting proxy settings would
also be surprising even without that platform bug.
"""

from __future__ import annotations

from typing import Any

import httpx


class ServiceHttpError(RuntimeError):
    """A first-party service returned a non-success HTTP status."""

    def __init__(self, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class ServiceHttpClient:
    """Small synchronous transport shared by first-party LAN service clients."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_s: float,
        error_label: str,
        include_url_in_errors: bool = False,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.error_label = error_label
        self.include_url_in_errors = include_url_in_errors

    def url(self, path: str) -> str:
        """Join a service-relative path without system URL handlers."""

        return f"{self.base_url}/{path.lstrip('/')}"

    def get_json(self, path: str) -> dict[str, Any] | list[Any]:
        """Fetch and decode a JSON response."""

        response = self._request("GET", path, headers={"Accept": "application/json"})
        return response.json()

    def post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        """POST a JSON object and decode the JSON response."""

        response = self._request(
            "POST",
            path,
            headers={"Accept": "application/json"},
            json=payload,
        )
        data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError(f"{self.error_label} returned non-object JSON")
        return data

    def get_bytes(self, path: str) -> bytes:
        """Fetch an opaque binary response."""

        return self._request("GET", path, headers={"Accept": "*/*"}).content

    def _request(
        self,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> httpx.Response:
        url = self.url(path)
        with httpx.Client(
            timeout=self.timeout_s,
            trust_env=False,
            follow_redirects=True,
        ) as client:
            response = client.request(method, url, **kwargs)
        if response.is_error:
            location = f" for {url}" if self.include_url_in_errors else ""
            raise ServiceHttpError(
                f"{self.error_label}{location}: "
                f"{response.status_code} {response.text}",
                status_code=response.status_code,
            )
        return response
