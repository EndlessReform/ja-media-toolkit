"""Service discovery precedence across native and deployed runtimes."""

from ja_media_core.services import service_base_url


def test_deployment_gateway_does_not_require_personal_config(monkeypatch) -> None:
    monkeypatch.setenv(
        "JA_MEDIA_SERVICES_ROOT_URL",
        "http://services.example.internal",
    )

    assert service_base_url(None, (), "/api/v1/anilist") == (
        "http://services.example.internal/api/v1/anilist"
    )


def test_direct_service_override_wins_over_deployment_gateway(monkeypatch) -> None:
    monkeypatch.setenv(
        "JA_MEDIA_SERVICES_ROOT_URL",
        "http://services.example.internal",
    )

    assert service_base_url(
        None, ("http://anilist-direct:8000",), "/api/v1/anilist"
    ) == "http://anilist-direct:8000"
