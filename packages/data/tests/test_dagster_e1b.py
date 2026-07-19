"""Contract tests for the deliberately narrow delayed-worker proof."""

import pytest

from ja_media_data.orchestration.dagster.e1b_job import e1b_delayed_vad
from ja_media_data.orchestration.dagster.e1b_storage import (
    output_fingerprint,
    output_manifest_key,
    selection_entries,
)


def test_distributed_job_routes_each_step_to_one_capability_queue() -> None:
    tags = {
        name: definition.tags["dagster-celery/queue"]
        for name, definition in e1b_delayed_vad.graph.node_dict.items()
    }

    assert tags == {
        "validate_canonical_slice": "server",
        "apple_vad_segments": "apple-vad",
        "verify_vad_segments": "server",
    }


def test_output_identity_is_stable_and_input_sensitive() -> None:
    first = output_fingerprint("source-a")

    assert first == output_fingerprint("source-a")
    assert first != output_fingerprint("source-b")
    assert output_manifest_key("source-a") == f"phase-e1b/vad/{first}/manifest.json"


def test_selection_requires_three_to_five_unique_locators() -> None:
    valid = {
        "schema_version": 1,
        "episodes": [
            {"locator": "anilist:1:01"},
            {"locator": "anilist:1:02"},
            {"locator": "anilist:1:03"},
        ],
    }

    assert len(selection_entries(valid)) == 3
    with pytest.raises(ValueError, match="three to five"):
        selection_entries({"schema_version": 1, "episodes": valid["episodes"][:2]})
    with pytest.raises(ValueError, match="unique"):
        selection_entries(
            {"schema_version": 1, "episodes": [valid["episodes"][0]] * 3}
        )
