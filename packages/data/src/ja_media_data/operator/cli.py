"""CLI adapter for the local operator web process."""

from __future__ import annotations

def run_web(*, port: int) -> None:
    """Serve the read-only workbench on loopback only."""

    if port < 1 or port > 65_535:
        raise SystemExit("--port must be between 1 and 65535")
    import uvicorn

    from ja_media_data.operator.http import create_operator_app

    uvicorn.run(
        create_operator_app(),
        host="127.0.0.1",
        port=port,
        log_level="info",
    )
