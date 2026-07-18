"""Borrow already-attached resources from the FastAPI-lifetime runtime."""

from collections.abc import Iterator

from fastapi import Request

from ja_media_data.operator.application import OperatorApplication


def get_application(request: Request) -> Iterator[OperatorApplication]:
    """Borrow a pooled application without client initialization or teardown."""

    with request.app.state.operator_runtime.application() as application:
        yield application
