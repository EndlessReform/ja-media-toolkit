"""Regression checks for the local source-mounted container contract."""

from pathlib import Path


REPOSITORY_ROOT = Path(__file__).parents[3]
LOCAL_COMPOSE = REPOSITORY_ROOT / "deploy/data/local/compose.yaml"


def test_local_apps_mount_the_complete_packages_workspace() -> None:
    """Keep sibling packages and migrations in sync without an image rebuild."""

    compose = LOCAL_COMPOSE.read_text()

    assert "../../../packages:/opt/ja-media/packages:ro" in compose
    assert "../../../packages/data/src:/opt/ja-media/packages/data/src:ro" not in compose
    assert 'command: ["ja-data-schema-init"]' in compose
