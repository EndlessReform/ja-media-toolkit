#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env"

if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
fi

MODEL_ID="${MODEL_ID:-Qwen/Qwen3-ForcedAligner-0.6B}"
SERVER_PORT="${SERVER_PORT:-8000}"
VLLM_IMAGE="${VLLM_IMAGE:-vllm/vllm-openai:latest}"
HF_HOME="${HF_HOME:-/var/lib/qwen3-forced-aligner/huggingface}"
MAX_NUM_SEQS="${MAX_NUM_SEQS:-1}"
GPU_MEMORY_UTILIZATION="${GPU_MEMORY_UTILIZATION:-0.90}"

mkdir -p "${HF_HOME}"

docker run --rm \
  --name qwen3-forced-aligner-vllm \
  --gpus all \
  --ipc=host \
  -p "${SERVER_PORT}:8000" \
  -e HF_HOME=/root/.cache/huggingface \
  -e HUGGING_FACE_HUB_TOKEN="${HF_TOKEN:-}" \
  -v "${HF_HOME}:/root/.cache/huggingface" \
  -v "${ROOT_DIR}/config:/config:ro" \
  --entrypoint vllm \
  "${VLLM_IMAGE}" \
  serve "${MODEL_ID}" \
  --host 0.0.0.0 \
  --port 8000 \
  --runner pooling \
  --enforce-eager \
  --chat-template /config/raw_content_chat_template.jinja \
  --hf-overrides '{"architectures":["Qwen3ASRForcedAlignerForTokenClassification"]}' \
  --max-num-seqs "${MAX_NUM_SEQS}" \
  --gpu-memory-utilization "${GPU_MEMORY_UTILIZATION}" \
  ${EXTRA_VLLM_ARGS:-}
