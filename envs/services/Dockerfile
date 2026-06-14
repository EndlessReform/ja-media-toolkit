FROM ghcr.io/astral-sh/uv:python3.13-trixie-slim

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    UV_NO_DEV=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/envs/services

# Install dependencies in their own layer. The services env depends on
# packages/core by path, so copy that package before syncing.
COPY packages/core /app/packages/core
COPY envs/services/pyproject.toml envs/services/uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY README.md /app/README.md
COPY envs/services /app/envs/services
RUN uv sync --locked --no-dev

ENTRYPOINT ["/app/envs/services/docker-entrypoint.sh"]
CMD ["uv", "run", "--no-sync", "anime-crosswalk"]
