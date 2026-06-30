ARG VLLM_BASE_IMAGE=vllm/vllm-openai:v0.24.0
FROM ${VLLM_BASE_IMAGE}

ARG VLLM_AUDIO_EXTRA_VERSION=0.24.0

# vLLM intentionally omits optional audio dependencies from its published image.
# The forced-aligner /pooling path accepts audio inputs, so this deployment owns
# the small matching extra layer instead of relying on a mutable upstream tag.
RUN uv pip install --system "vllm[audio]==${VLLM_AUDIO_EXTRA_VERSION}"
