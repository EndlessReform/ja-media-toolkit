"""DuckLake catalog access and checked-in lakehouse schema management."""

from ja_media_data.lakehouse.catalog import (
    CatalogConfig,
    apply_schema,
    connect_catalog,
)

__all__ = ["CatalogConfig", "apply_schema", "connect_catalog"]
