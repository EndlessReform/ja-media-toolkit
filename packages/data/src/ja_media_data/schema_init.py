"""One-shot additive schema entrypoint for deployment tooling."""

from __future__ import annotations

import json

import psycopg

from ja_media_data.lakehouse import CatalogConfig, apply_schema, connect_catalog
from ja_media_data.storage.binding_overrides import control_schema_from_settings
from ja_media_data.storage.binding_schema import (
    apply_postgres_schema,
    postgres_url_for_psycopg,
)


def main() -> None:
    config = CatalogConfig.from_settings()
    control_schema = control_schema_from_settings(
        catalog_schema=config.metadata_schema
    )
    with connect_catalog(config) as connection, psycopg.connect(
        postgres_url_for_psycopg(config.postgres_url), autocommit=True
    ) as control_connection:
        lakehouse = apply_schema(connection)
        postgres = apply_postgres_schema(
            control_connection, control_schema=control_schema
        )
    print(json.dumps({"lakehouse": lakehouse, "postgres_control": postgres}))
