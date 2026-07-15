"""Opt-in repository integration test against disposable local PostgreSQL."""

from datetime import UTC, datetime
import os
from uuid import uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.engine import make_url

from ja_media_data.database import create_ledger_engine, create_session_factory
from ja_media_data.models import BronzeCapture, CurrentEpisodeBinding, EpisodeBinding
from ja_media_data.repository import (
    BindingConflictError,
    BindingDecision,
    CaptureObservation,
    LedgerRepository,
)


TEST_DATABASE_URL_ENV = "JA_MEDIA_DATA_TEST_DATABASE_URL"


def test_postgresql_point_lookup_and_binding_conflict() -> None:
    """Exercise the migrated constraints and repository on real PostgreSQL."""

    database_url = os.environ.get(TEST_DATABASE_URL_ENV)
    if database_url is None:
        pytest.skip(f"{TEST_DATABASE_URL_ENV} is not configured")

    parsed_url = make_url(database_url)
    if parsed_url.host not in {"127.0.0.1", "localhost"} or not (
        parsed_url.database or ""
    ).endswith("_test"):
        pytest.fail(
            f"{TEST_DATABASE_URL_ENV} must target a local *_test database"
        )

    suffix = uuid4().hex
    first_capture_id = f"capture-{suffix}-1"
    second_capture_id = f"capture-{suffix}-2"
    binding_id = f"binding-{suffix}-1"
    conflicting_binding_id = f"binding-{suffix}-2"
    engine = create_ledger_engine(database_url)
    repository = LedgerRepository(create_session_factory(engine))

    def observe(capture_id: str) -> None:
        repository.observe_capture(
            CaptureObservation(
                capture_id=capture_id,
                series_namespace="anilist",
                series_id="15451",
                manifest_bucket="integration-test",
                manifest_key=f"bronze/{capture_id}.json",
                manifest_etag="etag-v1",
                manifest_schema_version=2,
                observed_at=datetime.now(UTC),
            )
        )

    def bind(identifier: str, capture_id: str) -> BindingDecision:
        return BindingDecision(
            binding_id=identifier,
            namespace="anilist",
            series_id=suffix,
            episode="3",
            audio_capture_id=capture_id,
            decision_method="integration-test",
            decision_evidence={"test": True},
            input_data_version="etag-v1",
            recipe_version="integration-v1",
        )

    try:
        observe(first_capture_id)
        observe(second_capture_id)
        repository.accept_binding(bind(binding_id, first_capture_id))

        current = repository.get_current_binding("anilist", suffix, "3")
        assert current is not None
        assert current.binding_id == binding_id
        with pytest.raises(BindingConflictError):
            repository.accept_binding(
                bind(conflicting_binding_id, second_capture_id)
            )
    finally:
        with engine.begin() as connection:
            connection.execute(
                delete(CurrentEpisodeBinding).where(
                    CurrentEpisodeBinding.series_id == suffix
                )
            )
            connection.execute(
                delete(EpisodeBinding).where(
                    EpisodeBinding.binding_id.in_(
                        [binding_id, conflicting_binding_id]
                    )
                )
            )
            connection.execute(
                delete(BronzeCapture).where(
                    BronzeCapture.capture_id.in_(
                        [first_capture_id, second_capture_id]
                    )
                )
            )
        engine.dispose()
