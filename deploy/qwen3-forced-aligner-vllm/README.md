# Qwen3 Forced Aligner vLLM Deployment

This folder runs `Qwen/Qwen3-ForcedAligner-0.6B` behind stock vLLM plus a thin
FastAPI adapter. The adapter keeps vLLM's large `/pooling` tensor on the GPU
host and returns compact token timings to LAN clients.

It is intentionally separate from the root `compose.yaml`, which is the local
ja-media service stack. The adapter image is built from the repository checkout
because it reuses the tested Qwen prompt and timestamp-decoding code.

## Files

- `.env.example`: machine-local settings to copy to `.env`
- `Dockerfile`: local vLLM image layer that installs `vllm[audio]`
- `adapter.Dockerfile`: CPU-only FastAPI adapter image with ffmpeg
- `compose.yaml`: preferred single-node Docker Compose startup
- `config/raw_content_chat_template.jinja`: emits only the text item from the
  multimodal user message, which is required by the client-side timestamp-row
  extraction strategy
- `scripts/run-docker.sh`: plain Docker fallback for hosts without Compose
- `scripts/serve-vllm.sh`: adds scheduler flags only when explicitly configured
- `scripts/start-compose.sh`: build, audio-smoke, and start via Compose
- `scripts/smoke-image-audio.sh`: import check for vLLM's optional audio deps
- `scripts/smoke-health.sh`: liveness/model-list smoke check

## Host Requirements

- NVIDIA driver that supports the target GPU
- Docker with NVIDIA Container Toolkit
- outbound network access to pull the model unless the Hugging Face cache is
  already populated
- a Hugging Face token in `.env` if the model or account requires one

The adapter build currently requires the repository checkout. This is an
intentional constraint of the forced-alignment spike, not a durable service
packaging decision.

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
- `SERVER_PORT`: host port mapped to the compact adapter
- `HF_HOME`: host cache directory for model weights
- `MAX_NUM_BATCHED_TOKENS`: optional per-iteration token budget
- `packages/data/config.local.toml`: the existing Bronze endpoint, bucket,
  prefix, and addressing style
- `packages/data/.env.local`: the existing read-only Bronze credentials
- `ALIGNER_AUDIO_CACHE`: host directory for verified episode audio

vLLM's official OpenAI images do not include optional audio dependencies. The
Dockerfile installs `vllm[audio]` at the matching vLLM version so PyAV and
SoundFile are present when `/pooling` receives an audio item.

If the selected vLLM base image does not contain
`Qwen3ASRForcedAlignerForTokenClassification`, pin `VLLM_BASE_IMAGE` and
`VLLM_AUDIO_EXTRA_VERSION` to a vLLM release that does.

## Start With Compose

```bash
./scripts/start-compose.sh
```

Docker Compose automatically reads `.env` when it runs from this deployment
directory. The wrapper changes into this directory before calling Compose so the
same command works even if you launch it from another path. It also reads the
existing `packages/data/config.local.toml` and adjacent `.env.local`; no Bronze
values are copied into this deployment's `.env`.

To select a different existing data configuration, set
`JA_MEDIA_DATA_CONFIG_HOST` to its host path before running the wrapper. Its
adjacent secret file follows the normal `.env.<environment>` naming rule.

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

With both scheduler settings empty, the startup command is equivalent to:

```bash
vllm serve Qwen/Qwen3-ForcedAligner-0.6B \
  --runner pooling \
  --chat-template /config/raw_content_chat_template.jinja \
  --hf-overrides '{"architectures":["Qwen3ASRForcedAlignerForTokenClassification"]}'
```

The BECK 180-second arm measured 2,340 audio embedding tokens, so vLLM's prior
2,048-token budget could not accept that request. This is a minimum-length fact,
not a throughput setting. Use the compact-response stress command to test larger
token budgets and concurrency before putting either scheduler value in `.env`.

`--enforce-eager` is not part of the known forced-aligner contract. Add it only
as a troubleshooting flag if vLLM CUDA graph capture or compilation behavior
breaks this model/image combination.

LAN clients call the adapter's `/audio/cache` and `/align` routes. The adapter
downloads the pinned AC-3 once, decodes each requested crop to mono 16 kHz PCM,
calls `/pooling` on the private Docker network, and reduces the raw tensor to
token timings and distribution metrics. The Mac still owns cue/window selection,
episode-clock offsets, cue reconstruction, SRT output, and review artifacts.

The upstream Qwen/vLLM example treats forced alignment as word-level timestamp
classification. The expected prompt body is a sequence of client-chosen text
tokens followed by two timestamp markers:

```text
word1<timestamp><timestamp>word2<timestamp><timestamp>
```

The ja-media inference client uses that shape for its default Qwen policy. It
segments Japanese text with nagisa, skips punctuation-like `補助記号` tokens by
default, predicts word timings, and then merges those word timings back into
caller-owned groups such as SRT/ASS cues, source-text lines, or one untimed text
blob. Japanese segmentation and cue reconstruction remain outside the adapter;
the request already contains ordered alignment tokens.

The chat template emits only the text content from the multimodal request. The
audio item remains in the request body for vLLM's multimodal processor; it just
must not be rendered into the textual prompt.

## Security Boundary

Compose exposes only the unauthenticated compact adapter. Raw vLLM is reachable
only on the private Docker network. Bind the adapter only to a trusted LAN or
VPN and do not put it directly on the public internet.
