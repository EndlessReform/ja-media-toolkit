"""Read-only access to the existing DEV Silver and bronze configuration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tomllib
from urllib.parse import quote, unquote

import duckdb
from dotenv import dotenv_values

from ja_media_data.lakehouse.catalog import CatalogConfig, connect_catalog
from ja_media_data.storage.bronze import BronzeStore


REPO_ROOT = Path(__file__).resolve().parents[4]
DATA_ROOT = REPO_ROOT / "packages" / "data"


@dataclass(frozen=True)
class DevReadAccess:
    """Opaque clients needed to read the current DEV canonical product."""

    catalog: CatalogConfig
    bronze: BronzeStore

    @classmethod
    def from_repository_config(cls) -> DevReadAccess:
        """Load existing secret owners without creating a research dotenv."""

        root_env = dotenv_values(REPO_ROOT / ".env")
        bronze_env = dotenv_values(DATA_ROOT / ".env.local")
        worker_env = dotenv_values(DATA_ROOT / ".env.worker.dev")
        local_config = tomllib.loads((DATA_ROOT / "config.local.toml").read_text())
        bronze_config = local_config["bronze"]
        endpoint = str(bronze_config["endpoint_url"])
        catalog = CatalogConfig(
            postgres_url=_postgres_url(
                _required(root_env, "JA_MEDIA_DATA_DATABASE_URL")
            ),
            metadata_schema="ja_media_ducklake_dev",
            data_path="s3://ja-media-dev/ducklake/dev/",
            s3_endpoint_url=endpoint,
            s3_region="garage",
            s3_key_id=_required(
                worker_env, "JA_MEDIA_WORKER_STAGING_ACCESS_KEY_ID"
            ),
            s3_secret=_required(
                worker_env, "JA_MEDIA_WORKER_STAGING_SECRET_ACCESS_KEY"
            ),
        )
        bronze = BronzeStore(
            endpoint_url=endpoint,
            bucket=str(bronze_config["bucket"]),
            prefix=str(bronze_config["prefix"]),
            addressing_style=str(bronze_config.get("addressing_style", "path")),
            access_key_id=_required(
                bronze_env, "JA_MEDIA_BRONZE__ACCESS_KEY_ID"
            ),
            secret_access_key=_required(
                bronze_env, "JA_MEDIA_BRONZE__SECRET_ACCESS_KEY"
            ),
        )
        return cls(catalog=catalog, bronze=bronze)

    def connect_catalog(self) -> duckdb.DuckDBPyConnection:
        """Attach to DEV DuckLake without creating schemas or tables."""

        return connect_catalog(self.catalog, initialize_catalog=False)


def _required(values: dict[str, str | None], name: str) -> str:
    value = values.get(name)
    if not value:
        raise RuntimeError(f"{name} is missing from its existing owning env file")
    return value


def _postgres_url(value: str) -> str:
    """Encode URL-special legacy password characters without exposing them."""

    scheme, raw = value.split("://", 1)
    auth, target = raw.rsplit("@", 1)
    user, password = auth.split(":", 1)
    return (
        f"{scheme}://{quote(unquote(user), safe='')}:"
        f"{quote(unquote(password), safe='')}@{target}"
    )
