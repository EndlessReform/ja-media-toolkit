from __future__ import annotations

import urllib.parse
from collections.abc import Iterable

from ja_media_core.config import load_config


def gateway_service_url(root_url: str | None, gateway_path: str) -> str | None:
    """Return one service route beneath the shared ja-media API gateway.

    ``[services].root_url`` names the gateway root, not an individual backend.
    Caddy then dispatches stable API prefixes like ``/api/v1/crosswalk`` and
    ``/api/v1/subtitles`` to their direct service containers.
    """

    if root_url is None:
        return None
    return urllib.parse.urljoin(
        f"{root_url.rstrip('/')}/",
        gateway_path.lstrip("/"),
    )


def service_base_url(
    explicit_url: str | None,
    env_urls: Iterable[str | None],
    gateway_path: str,
) -> str | None:
    """Resolve a client base URL from direct overrides or the shared gateway.

    Constructor arguments and service-specific environment variables are exact
    backend URLs. If none are set, fall back to ``[services].root_url`` and add
    the service's gateway route.
    """

    configured_url = explicit_url or next((url for url in env_urls if url), None)
    if configured_url:
        return configured_url

    try:
        return gateway_service_url(load_config().services.root_url, gateway_path)
    except Exception:
        return None
