"""Regression checks for local and shared-DEV deployment contracts."""

from pathlib import Path


REPOSITORY_ROOT = Path(__file__).parents[3]
LOCAL_COMPOSE = REPOSITORY_ROOT / "deploy/data/local/compose.yaml"
DEV_CONTROL = REPOSITORY_ROOT / "deploy/data/dev/control"


def test_local_apps_mount_the_complete_packages_workspace() -> None:
    """Keep sibling packages and migrations in sync without an image rebuild."""

    compose = LOCAL_COMPOSE.read_text()

    assert "../../../packages:/opt/ja-media/packages:ro" in compose
    assert (
        "../../../packages/data/src:/opt/ja-media/packages/data/src:ro" not in compose
    )
    assert 'command: ["ja-data-schema-init"]' in compose


def test_dev_update_recreates_gateway_after_replaceable_applications() -> None:
    """Do not preflight Caddy against an operator container that was removed."""

    control = DEV_CONTROL.read_text()
    schema_migration = "compose run --rm --no-deps schema-init"
    application_replacement = "code-location server-worker operator-web; then"
    gateway_replacement = "compose up -d --no-deps --force-recreate --wait gateway"

    migration_index = control.index(schema_migration, control.index("update()"))
    application_index = control.index(application_replacement)
    gateway_index = control.index(gateway_replacement)
    preflight_index = control.index("if ! preflight", gateway_index)

    assert migration_index < application_index < gateway_index < preflight_index
