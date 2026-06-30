# Qwen3 Forced Aligner vLLM Deployment

This folder is the compute-plane bundle for running
`Qwen/Qwen3-ForcedAligner-0.6B` behind vLLM's OpenAI-compatible `/pooling`
endpoint.

It belongs under `deploy/` because it is self-contained infrastructure that can
be copied to a plain L40S/A100 host. It is intentionally separate from the root
`compose.yaml`, which is the local ja-media service stack. The GPU server does
not need this repo, `uv`, or any `ja_media_*` Python package.

## Files

- `.env.example`: machine-local settings to copy to `.env`
- `Dockerfile`: local vLLM image layer that installs `vllm[audio]`
- `compose.yaml`: preferred single-node Docker Compose startup
- `config/raw_content_chat_template.jinja`: emits only the text item from the
  multimodal user message, which is required by the client-side timestamp-row
  extraction strategy
- `scripts/run-docker.sh`: plain Docker fallback for hosts without Compose
- `scripts/start-compose.sh`: build, audio-smoke, and start via Compose
- `scripts/smoke-image-audio.sh`: import check for vLLM's optional audio deps
- `scripts/smoke-health.sh`: liveness/model-list smoke check

## Host Requirements

- NVIDIA driver that supports the target GPU
- Docker with NVIDIA Container Toolkit
- outbound network access to pull the model unless the Hugging Face cache is
  already populated
- a Hugging Face token in `.env` if the model or account requires one

No repo checkout is required on the GPU host. Copy only this folder if desired.

## Configure

```bash
cp .env.example .env
$EDITOR .env
```

Key settings:

- `MODEL_ID`: defaults to `Qwen/Qwen3-ForcedAligner-0.6B`
- `VLLM_BASE_IMAGE`: defaults to `vllm/vllm-openai:v0.24.0`
- `VLLM_AUDIO_EXTRA_VERSION`: defaults to `0.24.0`; keep this matched to the
  base image's vLLM version
- `VLLM_AUDIO_IMAGE`: local tag for the derived image, defaulting to
  `qwen3-forced-aligner-vllm:0.24.0-audio`
- `SERVER_PORT`: host port mapped to vLLM port `8000`
- `HF_HOME`: host cache directory for model weights
- `MAX_NUM_SEQS`: start with `1` for proof-of-value validation

vLLM's official OpenAI images do not include optional audio dependencies. The
Dockerfile installs `vllm[audio]` at the matching vLLM version so PyAV, librosa,
and soundfile are present when `/pooling` receives an audio item.

If the selected vLLM base image does not contain
`Qwen3ASRForcedAlignerForTokenClassification`, pin `VLLM_BASE_IMAGE` and
`VLLM_AUDIO_EXTRA_VERSION` to a vLLM release that does. Do not install ja-media
code on the GPU host to fix that; the server should remain generic vLLM.

## Start With Compose

```bash
./scripts/start-compose.sh
```

Docker Compose automatically reads `.env` when it runs from this deployment
directory. The wrapper changes into this directory before calling Compose so the
same command works even if you launch it from another path.

In another shell:

```bash
./scripts/smoke-health.sh
```

Useful operations:

```bash
docker compose ps
docker compose logs -f vllm
docker compose down
```

## Start With Plain Docker

```bash
./scripts/run-docker.sh
```

This uses the same `.env` and mounts the same raw chat template.

`run-docker.sh` always rebuilds the derived image with `--pull` before starting
the container.

## Expected vLLM Shape

The startup command is equivalent to:

```bash
vllm serve Qwen/Qwen3-ForcedAligner-0.6B \
  --runner pooling \
  --chat-template /config/raw_content_chat_template.jinja \
  --hf-overrides '{"architectures":["Qwen3ASRForcedAlignerForTokenClassification"]}'
```

`--enforce-eager` is not part of the known forced-aligner contract. Add it only
as a troubleshooting flag if vLLM CUDA graph capture or compilation behavior
breaks this model/image combination.

The ja-media client will call `/pooling` with `task: "token_classify"`, a single
text/audio user message, and application-chosen `<timestamp>` markers. The
server owns model loading, audio decoding, feature extraction, and logits
generation. The client owns span selection, prompt construction, timestamp-row
extraction, cue correlation, and run serialization.

The chat template emits only the text content from the multimodal request. The
audio item remains in the request body for vLLM's multimodal processor; it just
must not be rendered into the textual prompt.

## Security Boundary

This deployment exposes an unauthenticated vLLM server. Bind it only where the
client can safely reach it, such as localhost, a private security group, SSH
tunnel, or a trusted VPN. Do not put it directly on the public internet.
