"""FastAPI adapter for the local operator workbench."""

from ja_media_data.operator.http.app import create_operator_app

__all__ = ["create_operator_app"]
