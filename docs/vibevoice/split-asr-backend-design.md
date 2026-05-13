# Split VibeVoice ASR Backend Design

This note sketches the first ASR backend shape for `ja-media-toolkit`, with
VibeVoice as the deliberately awkward first backend.

The non-negotiable constraint is that the Apple client must not download, store,
instantiate, or load the VibeVoice decoder stack. The Apple artifact should be a
small MLX-specific safetensors package containing only:

- VibeVoice acoustic tokenizer encoder weights
- VibeVoice semantic tokenizer encoder weights
- acoustic and semantic connector weights
- Qwen WTE / `embed_tokens` weights needed to build the initial prompt embeddings

Everything else belongs on the remote vLLM side or on a conversion machine.

## Target Split

```text
conversion machine, not the laptop:
  full VibeVoice checkpoint
  -> vibevoice-asr-mlx-encoder-wte/
  -> vibevoice-asr-vllm-rollout/

Apple client:
  decode/resample audio
  cut with VAD
  run VibeVoice acoustic + semantic encoders
  run speech connectors
  build VibeVoice prompt tokens
  run WTE for prompt text tokens
  splice speech features into prompt embeddings
  send full prompt embeddings to vLLM

remote vLLM server:
  receive prompt_embeds
  run Qwen prefill/decode
  return generated transcript text
```

The previous "load the full MLX wrapper first and trim later" plan is not
acceptable for this repo. It wastes laptop RAM and disk, and it also trains the
wrong abstraction into the codebase.

## Universal ASR Contract

The core ASR contract should stay small and runtime-free, following the existing
VAD split between `packages/core` contracts and `envs/apple` implementation
(`packages/core/src/ja_media_core/vad.py:47`,
`envs/apple/src/ja_media_apple/vad.py:39`).

Add:

```text
packages/core/src/ja_media_core/asr.py
```

Suggested contract:

```python
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, Sequence

from ja_media_core.audio import AudioChunk


AsrTask = Literal["transcribe", "translate"]


@dataclass(frozen=True)
class AsrOptions:
    language: str | None = "ja"
    task: AsrTask = "transcribe"
    context: str | None = None
    hotwords: tuple[str, ...] = ()
    timestamps: bool = True
    diarization: bool = False
    temperature: float = 0.0
    max_output_tokens: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AsrSegment:
    chunk: AudioChunk
    start_s: float
    end_s: float
    text: str
    speaker: str | None = None
    confidence: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AsrTranscript:
    chunk: AudioChunk
    text: str
    segments: list[AsrSegment]
    backend: str
    language: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class AsrBackend(Protocol):
    name: str

    def transcribe(
        self,
        chunks: Sequence[AudioChunk],
        *,
        options: AsrOptions | None = None,
    ) -> list[AsrTranscript]:
        ...
```

Why this belongs in core:

- `AudioChunk` is already the stable source-coordinate contract
  (`packages/core/src/ja_media_core/audio.py:36`).
- `InMemoryAudioChunk` is the decoded-array boundary and stays inside concrete
  backends (`packages/core/src/ja_media_core/audio.py:52`).
- `language`, context, hotwords, timestamps, and diarization are user intent,
  not model implementation details.
- VibeVoice prompt embeddings, vLLM URLs, tensor serialization, tokenizer
  patches, and safetensors layouts are backend config, not core contract.

`packages/core/src/ja_media_core/__init__.py` currently has a minimal
`Transcriber` sketch (`packages/core/src/ja_media_core/__init__.py:21`). Replace
that sketch with exports from `asr.py` when implementing this.

## Config Boundary

Use one user-facing TOML file for operator ergonomics, but parse it into
backend-specific settings in `envs/apple`. Core should not know about `base_url`
or model paths.

Example:

```toml
[asr]
default_backend = "vibevoice_split_vllm"

[asr.backends.vibevoice_split_vllm]
type = "vibevoice_split_vllm"
client_model_dir = "~/models/vibevoice-asr-mlx-encoder-wte"
vllm_base_url = "http://gpu-box.local:8000/v1"
vllm_model = "local/vibevoice-qwen-rollout"
tensor_transport = "torch_base64"
sample_rate_hz = 24000
max_chunk_s = 1800
timeout_s = 600

[asr.backends.whisper_local]
type = "whisper_local"
model_path = "~/models/whisper-large-v3-turbo"
device = "mlx"

[asr.backends.openai_compatible]
type = "openai_audio_transcriptions"
base_url = "http://gpu-box.local:8000/v1"
model = "Qwen/Qwen3-ASR-1.7B"
```

Suggested files:

- `packages/core/src/ja_media_core/asr.py`
- `envs/apple/src/ja_media_apple/asr_config.py`
- `envs/apple/src/ja_media_apple/asr_vibevoice_split.py`
- `envs/apple/src/ja_media_apple/vibevoice_encoder_wte.py`
- `envs/cuda/src/ja_media_cuda/model_prep/vibevoice_split.py`

The model-prep script lives under the environment that can safely run it, not
under an `artifacts` environment. "Artifact" is the output category; the
execution context is still a machine/dependency context. If the Nvidia
workstation environment is eventually named `envs/nvidia-linux` instead of
`envs/cuda`, put this there.

## Do Not Use the Upstream Loader

The upstream `mlx_audio` loader is the wrong primitive for the Apple side.

`base_load_model` constructs the full model object, then loads every safetensors
file into a `weights` dict, then sanitizes, then calls `model.load_weights`
(`docs/repo-symlinks/mlx-audio/mlx_audio/utils.py:390`,
`docs/repo-symlinks/mlx-audio/mlx_audio/utils.py:393`,
`docs/repo-symlinks/mlx-audio/mlx_audio/utils.py:397`,
`docs/repo-symlinks/mlx-audio/mlx_audio/utils.py:403`).

That is exactly what we must avoid.

The Apple backend needs a prepared-artifact loader:

```python
def load_vibevoice_encoder_wte(model_dir: Path) -> VibeVoiceEncoderWteModel:
    config = VibeVoiceEncoderWteConfig.from_json(model_dir / "config.json")
    model = VibeVoiceEncoderWteModel(config)
    weights = mx.load(str(model_dir / "model.safetensors"))
    model.load_weights(list(weights.items()), strict=True)
    model = VibeVoiceEncoderWteModel.post_load_hook(model, model_dir)
    model.eval()
    mx.eval(model.parameters())
    return model
```

This loader only opens the already-small client safetensors file. It never
mentions decoder layers and never calls `mlx_audio.stt.load(...)`.

## Encoder-WTE Model Sketch

The existing VibeVoice class proves the pieces are separable:

- `SpeechConnector` is standalone (`docs/repo-symlinks/mlx-audio/mlx_audio/stt/models/vibevoice_asr/vibevoice_asr.py:19`).
- Acoustic and semantic tokenizers are built before the language model
  (`docs/repo-symlinks/mlx-audio/mlx_audio/stt/models/vibevoice_asr/vibevoice_asr.py:108`).
- The decoder is only attached at `self.language_model = ...`
  (`docs/repo-symlinks/mlx-audio/mlx_audio/stt/models/vibevoice_asr/vibevoice_asr.py:124`).
- `encode_speech` is just tokenizer + connector work
  (`docs/repo-symlinks/mlx-audio/mlx_audio/stt/models/vibevoice_asr/vibevoice_asr.py:135`).
- Prompt construction and speech-pad masking are separate from generation
  (`docs/repo-symlinks/mlx-audio/mlx_audio/stt/models/vibevoice_asr/vibevoice_asr.py:835`).

So the vendored model should not subclass upstream `Model`; it should be a
smaller sibling with no decoder attributes:

```python
class VibeVoiceEncoderWteModel(nn.Module):
    """VibeVoice ASR client-side encoder and prompt embedder.

    This class intentionally has no Qwen decoder layers and no LM head. Its job
    is to produce the exact prompt embedding matrix expected by the remote Qwen
    rollout model.
    """

    def __init__(self, config: ModelConfig):
        super().__init__()
        self.config = config
        self.acoustic_tokenizer = AcousticTokenizerEncoder(
            config.acoustic_tokenizer_config
        )
        self.semantic_tokenizer = SemanticTokenizerEncoder(
            config.semantic_tokenizer_config
        )
        self.acoustic_connector = SpeechConnector(
            config.acoustic_vae_dim,
            config.decoder_config.hidden_size,
        )
        self.semantic_connector = SpeechConnector(
            config.semantic_vae_dim,
            config.decoder_config.hidden_size,
        )
        self.embed_tokens = nn.Embedding(
            config.decoder_config.vocab_size,
            config.decoder_config.hidden_size,
        )

    def encode_speech(self, speech_tensors: mx.array) -> mx.array:
        if speech_tensors.ndim == 1:
            speech_tensors = speech_tensors[None, :]
        if speech_tensors.ndim == 2:
            speech_tensors = speech_tensors[:, None, :]

        # For ASR, prefer deterministic means unless parity tests prove the
        # official path really depends on acoustic sampling.
        acoustic_tokens = self.acoustic_tokenizer.encode(speech_tensors)
        semantic_tokens = self.semantic_tokenizer.encode(speech_tensors)
        acoustic_features = self.acoustic_connector(acoustic_tokens)
        semantic_features = self.semantic_connector(semantic_tokens)
        return acoustic_features + semantic_features

    def build_prompt_embeddings(
        self,
        audio: mx.array,
        *,
        audio_duration_s: float,
        context: str | None = None,
    ) -> VibeVoicePromptEmbeddings:
        speech_features = self.encode_speech(audio)
        input_ids, speech_mask = self.build_prompt_tokens(
            speech_features=speech_features,
            audio_duration_s=audio_duration_s,
            context=context,
        )
        text_embeds = self.embed_tokens(input_ids)
        embeddings = splice_speech_features(
            text_embeds=text_embeds,
            speech_features=speech_features,
            speech_mask=speech_mask,
        )
        return VibeVoicePromptEmbeddings(
            input_ids=input_ids,
            embeddings=embeddings[0],
            speech_token_count=speech_features.shape[1],
        )
```

`build_prompt_tokens` should be copied from `_build_prompt_tokens`, including the
same chat template, speech start/end/pad tokens, and JSON segment request
(`docs/repo-symlinks/mlx-audio/mlx_audio/stt/models/vibevoice_asr/vibevoice_asr.py:847`,
`docs/repo-symlinks/mlx-audio/mlx_audio/stt/models/vibevoice_asr/vibevoice_asr.py:854`,
`docs/repo-symlinks/mlx-audio/mlx_audio/stt/models/vibevoice_asr/vibevoice_asr.py:884`).

`post_load_hook` should only load tokenizer files and special-token IDs, copied
from the upstream hook (`docs/repo-symlinks/mlx-audio/mlx_audio/stt/models/vibevoice_asr/vibevoice_asr.py:393`).

## Apple Safetensors Artifact

The Apple artifact should look like this:

```text
vibevoice-asr-mlx-encoder-wte/
  config.json
  tokenizer.json
  tokenizer_config.json
  special_tokens_map.json
  model.safetensors
```

Allowed tensor names in `model.safetensors`:

```text
acoustic_tokenizer.*
semantic_tokenizer.*
acoustic_connector.*
semantic_connector.*
embed_tokens.weight
```

Explicitly forbidden:

```text
language_model.model.layers.*
language_model.model.norm.*
language_model.lm_head.*
lm_head.*
```

The client artifact should use MLX-ready tensor layouts. That means conversion
has already handled the VibeVoice sanitizer's remaps and Conv1d transposes
(`docs/repo-symlinks/mlx-audio/mlx_audio/stt/models/vibevoice_asr/vibevoice_asr.py:288`,
`docs/repo-symlinks/mlx-audio/mlx_audio/stt/models/vibevoice_asr/vibevoice_asr.py:370`).

For the WTE, remap:

```text
language_model.model.embed_tokens.weight -> embed_tokens.weight
```

That keeps the client class flat and prevents accidentally recreating
`language_model`.

## Conversion Script Shape

Add an off-laptop conversion command in the Nvidia/Linux environment, for
example:

```text
envs/cuda/src/ja_media_cuda/model_prep/vibevoice_split.py
```

Run it from the environment that is allowed to hold the full checkpoint:

```sh
cd envs/cuda
uv run ja-media-cuda prepare-vibevoice-split \
  --source microsoft/VibeVoice-ASR \
  --mlx-client-out /models/vibevoice-asr-mlx-encoder-wte \
  --vllm-out /models/vibevoice-asr-vllm-rollout
```

The source code belongs with the heavy runtime because this script's dependency
and resource assumptions are part of the Nvidia/Linux world: large system RAM,
Torch/safetensors/Transformers, and possibly local access to the vLLM rollout
checkpoint. Its outputs are the only artifacts copied to the laptop.

High-level flow:

```python
CLIENT_PREFIXES = (
    "acoustic_tokenizer.",
    "semantic_tokenizer.",
    "acoustic_connector.",
    "semantic_connector.",
)


def keep_client_weight(key: str) -> bool:
    return (
        key.startswith(CLIENT_PREFIXES)
        or key == "language_model.model.embed_tokens.weight"
    )


def client_key(key: str) -> str:
    if key == "language_model.model.embed_tokens.weight":
        return "embed_tokens.weight"
    return key
```

The conversion can either:

1. Use the upstream sanitizer on a non-laptop machine, then filter and write the
   client artifact.
2. Preferably stream with `safetensors.safe_open`, filter keys before loading
   tensors into memory, and apply the same key remaps/transposes as
   `Model.sanitize`.

Option 2 is better engineering, but option 1 is still acceptable because the
full checkpoint never touches the laptop. The resulting `model.safetensors`
must be audited before use:

```python
bad = [
    key for key in saved_keys
    if key.startswith("language_model.model.layers.")
    or key.startswith("language_model.model.norm.")
    or key.startswith("language_model.lm_head.")
    or key.startswith("lm_head.")
]
if bad:
    raise RuntimeError(f"client artifact contains decoder weights: {bad[:5]}")
```

The vLLM artifact is a separate output. It should contain the Qwen rollout
weights and tokenizer/config patching needed by the server. With stock vLLM,
expect the server to still need its own WTE for generated token decode, even if
the prompt prefill arrives as embeddings. That is fine: the server has the RAM.
The laptop artifact is still encoder + WTE only.

## vLLM Request Path

Use full-prompt embeddings through Completions because the client is doing WTE
for the whole initial prompt.

The local vLLM note says Completions accepts `prompt_embeds`
(`docs/vibevoice/vllm-embed-interface.md:49`) and that the caller must build the
full already-templated prompt representation
(`docs/vibevoice/vllm-embed-interface.md:72`).

The request shape is:

```python
prompt_embeds = to_torch_2d(client_embeddings)  # (seq, hidden)
encoded = tensor2base64(prompt_embeds)

client.completions.create(
    model=config.vllm_model,
    prompt=None,
    max_tokens=options.max_output_tokens or 8192,
    temperature=options.temperature,
    extra_body={"prompt_embeds": encoded},
)
```

Requirements:

- vLLM starts with `--enable-prompt-embeds`
  (`docs/vibevoice/vllm-embed-interface.md:112`).
- HTTP payloads currently use base64-encoded `torch.save` tensors
  (`docs/vibevoice/vllm-embed-interface.md:133`).
- Each request sends a 2-D `(num_tokens, hidden_size)` tensor
  (`docs/vibevoice/vllm-embed-interface.md:123`).

The Apple backend can import PyTorch purely for serialization if that is the
least annoying first bridge. The model weights on the Apple side remain MLX
safetensors.

## Implementation Order

1. Add `packages/core/src/ja_media_core/asr.py`.
2. Vendor `envs/apple/src/ja_media_apple/vibevoice_encoder_wte.py` with a
   decoder-free `VibeVoiceEncoderWteModel`.
3. Add the prepared-artifact loader that only opens
   `vibevoice-asr-mlx-encoder-wte/model.safetensors`.
4. Add the off-laptop split-artifact command under `envs/cuda` and make it fail
   if the client safetensors contains decoder keys.
5. Add `envs/apple/src/ja_media_apple/asr_vibevoice_split.py` to connect
   `AudioChunk` materialization, encoder-WTE prompt building, vLLM request, and
   transcript parsing.
6. Smoke test with a tiny clip by comparing prompt token IDs, prompt embedding
   shape, and generated transcript against a full reference run on the machine
   that is allowed to hold the full model.

## Residual Risks

- Acoustic determinism: upstream acoustic encoding may sample when using
  `AcousticTokenizerEncoder.__call__`
  (`docs/repo-symlinks/mlx-audio/mlx_audio/stt/models/vibevoice_asr/audio_encoder.py:650`).
  The client class should call `.encode(...)` first and only switch if parity
  testing proves sampling is required.
- Prompt length: `_build_prompt_tokens` repeats `<|box_start|>` once per speech
  feature row (`docs/repo-symlinks/mlx-audio/mlx_audio/stt/models/vibevoice_asr/vibevoice_asr.py:876`).
  VAD chunking should cap duration before prompt embeddings become silly.
- Server WTE: stock autoregressive decode normally needs WTE for generated
  tokens after the prompt prefill. Do not spend laptop RAM or disk on that
  problem; keep it server-side unless a later custom vLLM runner removes it.
- Tokenizer mismatch: the Apple artifact must include the exact tokenizer files
  used to build the server checkpoint. Prompt-embed bugs here will be quiet and
  deeply irritating.
