#!/usr/bin/env bash
set -euo pipefail

args=(
  serve "${MODEL_ID:-Qwen/Qwen3-ForcedAligner-0.6B}"
  --host 0.0.0.0
  --port 8000
  --runner pooling
  --chat-template /config/raw_content_chat_template.jinja
  --hf-overrides '{"architectures":["Qwen3ASRForcedAlignerForTokenClassification"]}'
)

if [[ -n "${GPU_MEMORY_UTILIZATION:-}" ]]; then
  args+=(--gpu-memory-utilization "${GPU_MEMORY_UTILIZATION}")
fi
if [[ -n "${MAX_NUM_SEQS:-}" ]]; then
  args+=(--max-num-seqs "${MAX_NUM_SEQS}")
fi
if [[ -n "${MAX_NUM_BATCHED_TOKENS:-}" ]]; then
  args+=(--max-num-batched-tokens "${MAX_NUM_BATCHED_TOKENS}")
fi
if [[ -n "${EXTRA_VLLM_ARGS:-}" ]]; then
  read -r -a extra_args <<<"${EXTRA_VLLM_ARGS}"
  args+=("${extra_args[@]}")
fi

exec vllm "${args[@]}"
