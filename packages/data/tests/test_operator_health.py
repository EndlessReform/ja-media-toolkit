"""Process-readiness contract for the operator HTTP adapter."""

from fastapi.testclient import TestClient

from ja_media_data.operator.http.app import create_operator_app
from operator_test_support import NoopOperatorRuntime


def test_healthz_reports_completed_startup() -> None:
    app = create_operator_app(runtime_factory=NoopOperatorRuntime)

    with TestClient(app) as client:
        response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
