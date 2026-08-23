#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "${ROOT_DIR}/../.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env"

if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
fi

: "${ALIGNER_BRONZE_ENDPOINT_URL:?set in deploy/qwen3-forced-aligner-vllm/.env}"
: "${ALIGNER_BRONZE_BUCKET:?set in deploy/qwen3-forced-aligner-vllm/.env}"

SERVER_PORT="${SERVER_PORT:-8000}"
VLLM_BASE_IMAGE="${VLLM_BASE_IMAGE:-vllm/vllm-openai:v0.24.0}"
VLLM_AUDIO_EXTRA_VERSION="${VLLM_AUDIO_EXTRA_VERSION:-0.24.0}"
VLLM_AUDIO_IMAGE="${VLLM_AUDIO_IMAGE:-qwen3-forced-aligner-vllm:0.24.0-audio}"
ADAPTER_IMAGE="${ALIGNER_ADAPTER_IMAGE:-qwen3-forced-aligner-adapter:local}"
HF_HOME="${HF_HOME:-/var/lib/qwen3-forced-aligner/huggingface}"
AUDIO_CACHE="${ALIGNER_AUDIO_CACHE:-/var/lib/qwen3-forced-aligner/audio}"
NETWORK="qwen3-forced-aligner"
VLLM_CONTAINER="qwen3-forced-aligner-vllm-raw"

mkdir -p "${HF_HOME}" "${AUDIO_CACHE}"

docker build \
  --pull \
  --build-arg "VLLM_BASE_IMAGE=${VLLM_BASE_IMAGE}" \
  --build-arg "VLLM_AUDIO_EXTRA_VERSION=${VLLM_AUDIO_EXTRA_VERSION}" \
  -t "${VLLM_AUDIO_IMAGE}" \
  "${ROOT_DIR}"

docker build \
  --pull \
  -f "${ROOT_DIR}/adapter.Dockerfile" \
  -t "${ADAPTER_IMAGE}" \
  "${REPO_ROOT}"

docker network inspect "${NETWORK}" >/dev/null 2>&1 \
  || docker network create "${NETWORK}" >/dev/null
docker rm --force "${VLLM_CONTAINER}" >/dev/null 2>&1 || true

cleanup() {
  docker rm --force "${VLLM_CONTAINER}" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

docker run --detach \
  --name "${VLLM_CONTAINER}" \
  --network "${NETWORK}" \
  --gpus all \
  --ipc=host \
  -e HF_HOME=/root/.cache/huggingface \
  -e HUGGING_FACE_HUB_TOKEN="${HF_TOKEN:-}" \
  -e MODEL_ID="${MODEL_ID:-Qwen/Qwen3-ForcedAligner-0.6B}" \
  -e GPU_MEMORY_UTILIZATION="${GPU_MEMORY_UTILIZATION:-}" \
  -e MAX_NUM_SEQS="${MAX_NUM_SEQS:-}" \
  -e MAX_NUM_BATCHED_TOKENS="${MAX_NUM_BATCHED_TOKENS:-}" \
  -e EXTRA_VLLM_ARGS="${EXTRA_VLLM_ARGS:-}" \
  -v "${HF_HOME}:/root/.cache/huggingface" \
  -v "${ROOT_DIR}/config:/config:ro" \
  --entrypoint /usr/local/bin/serve-vllm \
  "${VLLM_AUDIO_IMAGE}"

docker run --rm \
  --name qwen3-forced-aligner-adapter \
  --network "${NETWORK}" \
  -p "${SERVER_PORT}:8000" \
  -e HF_HOME=/root/.cache/huggingface \
  -e ALIGNER_VLLM_BASE_URL="http://${VLLM_CONTAINER}:8000" \
  -e ALIGNER_AUDIO_CACHE_DIR=/var/cache/ja-media/audio \
  -e ALIGNER_BRONZE_ENDPOINT_URL \
  -e ALIGNER_BRONZE_BUCKET \
  -e ALIGNER_BRONZE_PREFIX="${ALIGNER_BRONZE_PREFIX:-audio/anime/bronze-v2}" \
  -e ALIGNER_BRONZE_ADDRESSING_STYLE="${ALIGNER_BRONZE_ADDRESSING_STYLE:-path}" \
  -e ALIGNER_BRONZE_ACCESS_KEY_ID="${ALIGNER_BRONZE_ACCESS_KEY_ID:-}" \
  -e ALIGNER_BRONZE_SECRET_ACCESS_KEY="${ALIGNER_BRONZE_SECRET_ACCESS_KEY:-}" \
  -v "${HF_HOME}:/root/.cache/huggingface" \
  -v "${AUDIO_CACHE}:/var/cache/ja-media/audio" \
  "${ADAPTER_IMAGE}"
