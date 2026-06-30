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

VLLM_AUDIO_IMAGE="${VLLM_AUDIO_IMAGE:-qwen3-forced-aligner-vllm:0.24.0-audio}"

docker run --rm \
  --entrypoint python3 \
  "${VLLM_AUDIO_IMAGE}" \
  -c "import av, librosa, soundfile; print('vLLM audio dependencies import')"
