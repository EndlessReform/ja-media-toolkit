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
  -c "import io; import av; import numpy as np; import soundfile as sf; from vllm.multimodal.media.audio import load_audio; buffer = io.BytesIO(); sf.write(buffer, np.zeros(1600, dtype=np.float32), 16000, format='WAV'); buffer.seek(0); audio, rate = load_audio(buffer, sr=16000); assert rate == 16000 and audio.shape == (1600,); print('vLLM WAV decode smoke passed')"
