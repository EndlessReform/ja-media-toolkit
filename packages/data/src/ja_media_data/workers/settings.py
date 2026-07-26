"""Checked capability profiles composed with narrowly scoped local secrets."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tomllib
from urllib.parse import quote

from dotenv import dotenv_values


PACKAGE_ROOT = Path(__file__).parents[3]
DEFAULT_PROFILES_PATH = PACKAGE_ROOT / "worker_profiles.toml"
DEFAULT_LOCAL_CONFIG = PACKAGE_ROOT / "config.local.toml"
DEFAULT_LOCAL_ENV = PACKAGE_ROOT / ".env.local"
DEFAULT_WORKER_ENV = PACKAGE_ROOT / ".env.worker.dev"


@dataclass(frozen=True)
class WorkerProfile:
    """One native machine's supported queue and object-store boundary."""

    name: str
    broker_host: str
    broker_port: int
    broker_user: str
    broker_vhost: str
    queue: str
    operations: tuple[str, ...]
    concurrency: int
    staging_bucket: str
    staging_prefix: str
    staging_region: str


@dataclass(frozen=True)
class WorkerSecrets:
    """Only secrets permitted in the native worker process."""

    rabbitmq_password: str
    bronze_access_key_id: str
    bronze_secret_access_key: str
    staging_access_key_id: str
    staging_secret_access_key: str


@dataclass(frozen=True)
class WorkerSettings:
    """Resolved profile plus secrets, with no database configuration."""

    profile: WorkerProfile
    secrets: WorkerSecrets
    bronze_endpoint_url: str
    bronze_bucket: str
    bronze_prefix: str
    bronze_addressing_style: str
    loaded_files: tuple[Path, Path, Path]

    @property
    def broker_url(self) -> str:
        """Build the URL without persisting it in another dotenv file."""

        user = quote(self.profile.broker_user, safe="")
        password = quote(self.secrets.rabbitmq_password, safe="")
        vhost = quote(self.profile.broker_vhost, safe="")
        return (
            f"pyamqp://{user}:{password}@{self.profile.broker_host}:"
            f"{self.profile.broker_port}/{vhost}"
        )


def load_worker_settings(
    profile_name: str,
    *,
    profiles_path: Path = DEFAULT_PROFILES_PATH,
    local_config: Path = DEFAULT_LOCAL_CONFIG,
    local_env: Path = DEFAULT_LOCAL_ENV,
    worker_env: Path = DEFAULT_WORKER_ENV,
) -> WorkerSettings:
    """Load the checked profile and exactly two documented secret files."""

    document = tomllib.loads(profiles_path.read_text())
    local_document = tomllib.loads(local_config.read_text())
    raw = document.get("profiles", {}).get(profile_name)
    if not isinstance(raw, dict):
        raise ValueError(f"unknown worker profile: {profile_name}")
    local = dotenv_values(local_env)
    worker = dotenv_values(worker_env)
    secrets = WorkerSecrets(
        rabbitmq_password=_required(worker, "RABBITMQ_PASSWORD", worker_env),
        bronze_access_key_id=_required(
            local, "JA_MEDIA_BRONZE__ACCESS_KEY_ID", local_env
        ),
        bronze_secret_access_key=_required(
            local, "JA_MEDIA_BRONZE__SECRET_ACCESS_KEY", local_env
        ),
        staging_access_key_id=_required(
            worker, "JA_MEDIA_WORKER_STAGING_ACCESS_KEY_ID", worker_env
        ),
        staging_secret_access_key=_required(
            worker, "JA_MEDIA_WORKER_STAGING_SECRET_ACCESS_KEY", worker_env
        ),
    )
    profile = WorkerProfile(
        name=profile_name,
        broker_host=str(raw["broker_host"]),
        broker_port=int(raw["broker_port"]),
        broker_user=str(raw["broker_user"]),
        broker_vhost=str(raw["broker_vhost"]),
        queue=str(raw["queue"]),
        operations=tuple(str(item) for item in raw["operations"]),
        concurrency=int(raw["concurrency"]),
        staging_bucket=str(raw["staging_bucket"]),
        staging_prefix=str(raw["staging_prefix"]).strip("/"),
        staging_region=str(raw["staging_region"]),
    )
    bronze = local_document["bronze"]
    return WorkerSettings(
        profile=profile,
        secrets=secrets,
        bronze_endpoint_url=str(bronze["endpoint_url"]),
        bronze_bucket=str(bronze["bucket"]),
        bronze_prefix=str(bronze["prefix"]),
        bronze_addressing_style=str(bronze.get("addressing_style", "path")),
        loaded_files=(local_config, local_env, worker_env),
    )


def _required(values: dict[str, str | None], name: str, source: Path) -> str:
    value = values.get(name)
    if value is None or not value.strip():
        raise ValueError(f"{name} is missing from {source}")
    return value
