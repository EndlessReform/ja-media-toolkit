FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.8.22 /uv /bin/uv

RUN apt-get update \
    && apt-get install --yes --no-install-recommends curl ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
COPY packages/core packages/core
COPY packages/frontend packages/frontend
COPY envs/inference envs/inference
COPY research/forced-alignment-retiming/cases.toml research/forced-alignment-retiming/cases.toml
COPY deploy/qwen3-forced-aligner-vllm/review_workbench deploy/qwen3-forced-aligner-vllm/review_workbench

# The disposable workbench imports only frontend's deterministic SRT-cleaning
# modules. Installing that package without its playback/TUI dependency tree keeps
# the image small and avoids treating this experiment as a frontend deployment.
RUN uv pip install --system ./packages/core ./envs/inference 'pillow>=12.3.0' \
    && uv pip install --system --no-deps ./packages/frontend ./deploy/qwen3-forced-aligner-vllm/review_workbench

EXPOSE 8000
CMD ["uvicorn", "ja_media_inference.forced_alignment.adapter_main:app", "--host", "0.0.0.0", "--port", "8000"]
