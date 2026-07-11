ARG DAGSTER_VERSION=1.13.12
ARG DAGSTER_LIBRARY_VERSION=0.29.12
FROM python:3.12-slim

ARG DAGSTER_VERSION
ARG DAGSTER_LIBRARY_VERSION

COPY --from=ghcr.io/astral-sh/uv:0.8.22 /uv /uvx /bin/

RUN apt-get update \
    && apt-get install --no-install-recommends --yes curl \
    && rm -rf /var/lib/apt/lists/* \
    && uv pip install --system \
        "dagster==${DAGSTER_VERSION}" \
        "dagster-postgres==${DAGSTER_LIBRARY_VERSION}" \
        "dagster-webserver==${DAGSTER_VERSION}"

ENV DAGSTER_HOME=/opt/dagster/home
WORKDIR /opt/dagster/app

COPY dagster.yaml /opt/dagster/home/dagster.yaml
COPY workspace.yaml /opt/dagster/home/workspace.yaml
COPY definitions.py /opt/dagster/app/definitions.py
