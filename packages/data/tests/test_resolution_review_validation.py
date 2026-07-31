"""Validation at the HTTP boundary before a model stream begins."""

from fastapi.testclient import TestClient

from ja_media_data.operator.http.app import create_operator_app


class UnconfiguredRuntime:
    """Minimal runtime proving provider errors become ordinary API errors."""

    def start_review(self, anilist_id: int, choice):
        raise ValueError(
            "agent configuration is unset: model_id, base_url; enter both model "
            "and base URL in the browser or configure [agent]"
        )

    def close(self) -> None:
        pass


def test_unconfigured_model_is_reported_as_api_validation_error() -> None:
    runtime = UnconfiguredRuntime()
    app = create_operator_app(runtime_factory=lambda: runtime)

    with TestClient(app) as client:
        response = client.post(
            "/operator/resolution-review/series/15451/run",
            data={"model_id": "", "base_url": ""},
        )

    assert response.status_code == 422
    assert response.json() == {
        "detail": (
            "agent configuration is unset: model_id, base_url; enter both model "
            "and base URL in the browser or configure [agent]"
        )
    }
