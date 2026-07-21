"""Construction of the loopback-only operator HTTP adapter."""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from ja_media_data.operator.runtime import OperatorRuntime
from ja_media_data.operator.http.api_routes import router as api_router
from ja_media_data.operator.http.html_routes import router as html_router


PACKAGE_DIR = Path(__file__).parent


def create_operator_app(
    *,
    runtime_factory: Callable[[], OperatorRuntime] = OperatorRuntime,
) -> FastAPI:
    """Construct the adapter with one application-lifetime operator runtime.

    ``runtime_factory`` is an injection seam for HTTP tests. Schema migration is
    an explicit deployment one-shot and never part of WebUI startup.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        runtime = runtime_factory()
        app.state.operator_runtime = runtime
        try:
            yield
        finally:
            runtime.close()

    app = FastAPI(
        title="ja-data operator workbench",
        version="0.1.0",
        description="Read-only domain workbench joined to Dagster execution state.",
        lifespan=lifespan,
    )
    app.mount(
        "/operator/static",
        StaticFiles(directory=PACKAGE_DIR / "static"),
        name="operator-static",
    )
    app.include_router(api_router)
    app.include_router(html_router)

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse("/operator")

    return app
