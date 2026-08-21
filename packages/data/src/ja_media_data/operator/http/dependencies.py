"""Borrow already-attached resources from the FastAPI-lifetime runtime."""

from collections.abc import Iterator

from fastapi import Request

from ja_media_data.operator.application import OperatorApplication
from ja_media_data.operator.runtime import OperatorRuntime


def get_application(request: Request) -> Iterator[OperatorApplication]:
    """Borrow a pooled application without client initialization or teardown."""

    with request.app.state.operator_runtime.application() as application:
        yield application


def get_runtime(request: Request) -> OperatorRuntime:
    """Return the application-lifetime owner for streamed review sessions."""

    return request.app.state.operator_runtime
