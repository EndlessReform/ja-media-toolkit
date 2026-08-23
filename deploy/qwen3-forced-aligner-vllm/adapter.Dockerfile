FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.8.22 /uv /bin/uv

RUN apt-get update \
    && apt-get install --yes --no-install-recommends curl ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
COPY packages/core packages/core
COPY envs/inference envs/inference

RUN uv pip install --system ./packages/core ./envs/inference

EXPOSE 8000
CMD ["uvicorn", "ja_media_inference.forced_alignment.adapter_main:app", "--host", "0.0.0.0", "--port", "8000"]
