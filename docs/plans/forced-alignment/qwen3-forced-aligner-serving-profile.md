# Qwen3 Forced Aligner Serving Profile

## Outcome

The measured 60-second request spends about 20 ms in the vLLM engine and about
2.25 seconds inside the vLLM API process before response headers arrive. The
slow path is therefore not the model forward pass, the LAN, the adapter's audio
decode, or adapter-side reduction. Stock `/pooling` converts a
`[prompt positions, 5000]` tensor to nested Python lists and then decimal JSON.

`/pooling` is the correct vLLM runner for this non-autoregressive token
classifier. Its default float JSON representation is the wrong result contract
for this workload.

## Measured Request

The profiled Fumoffu episode 1 request used 60 seconds of audio and 166 text
tokens, with two timestamp slots per text token.

| Stage or result | Measured value |
| --- | ---: |
| vLLM prompt positions | 1,335 |
| timestamp classes per position | 5,000 |
| returned float values | 6,675,000 |
| vLLM JSON response | 149,244,274 bytes |
| vLLM engine end-to-end trace | 19.95 ms |
| vLLM model inference trace | 12.13 ms |
| adapter request until vLLM headers | 2.250 s |
| vLLM body transfer | 45.6 ms |
| adapter JSON parse | 1.155 s |
| adapter timestamp reduction | 321 ms |
| compact adapter response plus LAN | 101 ms |

A second request used the same audio but only one text token. It returned 786
rows and 87,876,925 bytes. Request-to-headers fell to 1.373 seconds and JSON
parsing fell to 631 ms. Holding audio constant while changing returned matrix
size locates the missing time in output conversion and serialization rather
than audio processing.

The two requests imply roughly 68 MB/s for the current conversion to decimal
JSON. That number is serialization throughput, not RAM or PCIe bandwidth.

## Trace Receipt

The vLLM OpenTelemetry request span supplies these keys:

| Key | Meaning in this profile |
| --- | --- |
| `gen_ai.latency.e2e` | Time owned by the vLLM engine request |
| `gen_ai.latency.time_in_model_inference` | GPU model execution |
| `gen_ai.latency.time_in_queue` | Scheduler queue wait |
| `gen_ai.latency.time_in_model_prefill` | Prefill portion |
| `gen_ai.latency.time_in_model_decode` | Decode portion; this classifier has no useful generation loop |
| `gen_ai.latency.time_to_first_token` | First engine output timing |
| `gen_ai.usage.prompt_tokens` | Expanded prompt positions; 1,335 in the measured request |
| `gen_ai.usage.completion_tokens` | Generated tokens; zero for this request |
| `gen_ai.request.id` | Correlation identifier |

The trace ends before HTTP headers are available to the adapter. vLLM 0.24's
pooling response code then calls `PoolingOutput.data.tolist()` and builds the
HTTP response. This brackets the 2.23-second gap inside the vLLM API process.
The current trace does not separately time device synchronization, `.tolist()`,
Pydantic construction, and ORJSON encoding; add spans around those calls if
that finer split becomes useful.

Source anchors for the deployed vLLM 0.24 release:

- [forced-aligner model and classification head](https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/model_executor/models/qwen3_asr_forced_aligner.py)
- [pooling HTTP response construction](https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/entrypoints/pooling/pooling/serving.py)
- [float and binary pooling encoders](https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/entrypoints/pooling/utils.py)

## Algorithm And Tensor Shapes

1. Audio processing and prompt construction produce 1,335 transformer input
   positions in the measured request.
2. Qwen3-0.6B emits a 1,024-value hidden state for every position:
   `[1335, 1024]`.
3. A linear timestamp head maps every hidden state from 1,024 values to 5,000
   time-bin logits: `[1335, 5000]`.
4. Only 332 rows are requested timestamp slots: two for each of 166 text
   tokens. The default `ALL` token pooler nevertheless returns all 1,335 rows.
5. For each timestamp row, the winning class index multiplied by 80 ms is the
   predicted time. Max probability, top-two margin, and normalized entropy use
   the same 5,000-class distribution as review signals.
6. The stock float encoder synchronizes the result into host-visible Python
   objects, formats 6.675 million numbers as JSON, and sends the matrix to the
   adapter. The adapter parses it, selects the 332 rows, and computes those
   values after the expensive transfer.

The Qwen3-ASR report describes the aligner in sections 3.2 through 3.4:
[model design, training, and non-autoregressive inference](https://arxiv.org/html/2601.21337#S3).
The original aligner paper gives the timestamp projection and softmax in
[section 3.2](https://arxiv.org/html/2601.18220#S3.SS2), followed by simultaneous
slot prediction in section 3.3. The report describes a 3,750-class training
configuration; the deployed checkpoint and vLLM model config use 5,000 classes.

## Practical Escape Hatches

### 1. Binary `/pooling`: diagnostic baseline

Request the existing binary pooling encoding instead of float JSON. The full
matrix is about 13.35 MB as fp16 or 26.7 MB as fp32, versus 149.2 MB as JSON.
This removes per-float Python and decimal formatting but still moves every row
to host memory. It is the quickest way to measure how much of the delay is JSON.

### 2. `STEP` token pooling: bounded implementation

vLLM's token-wise `StepPool` can select rows whose input token ID equals
`step_tag_id`, and can also select output columns with `returned_token_ids`.
Set the step tag to the checkpoint's timestamp token ID. Row selection then
happens on the GPU before the result crosses to the API process. For this
request that reduces 1,335 rows to 332 rows, or about 3.32 MB as fp16.

An out-of-tree IO processor can set the pooling parameters and turn the selected
rows into the adapter's compact response. Its post-processing hook alone is not
enough: that hook receives `PoolingRequestOutput` after engine output exists on
the API side. The GPU-side reduction comes from pairing it with `StepPool`.

Relevant extension points:

- [IO processor plugin design](https://docs.vllm.ai/en/v0.23.0/design/io_processor_plugins/)
- [vLLM 0.24 pooling IO processor source](https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/entrypoints/pooling/pooling/io_processor.py)
- [token-wise pooling methods](https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/model_executor/layers/pooler/tokwise/methods.py)

### 3. Custom GPU pooler: smallest result

If row selection is still too expensive, register an out-of-tree model or
pooler with `ModelRegistry`. On the GPU it can select timestamp rows, run
`log_softmax`, `topk`, and entropy reduction, and return only class index,
maximum probability, top-two margin, and entropy for each slot. That changes
the result from millions of floats to a few kilobytes.

[Out-of-tree model registration](https://docs.vllm.ai/en/v0.24.0/contributing/model/registration/)
provides this hook without maintaining a vLLM fork. vLLM 0.24 does not provide
the newer endpoint-plugin mechanism, so it is not a deployment option while the
image remains pinned to 0.24.

## Recommendation

The spike deployment now exposes raw `/pooling` on LAN port 8001 and starts the
model with `StepPool` using timestamp token ID `151705`. Measure float and binary
encodings against that endpoint. Add an IO processor only if transferring the
selected timestamp rows still matters.

Do not replace this with `/completions`. The forced-aligner architecture removes
the language-model head and applies a 5,000-class timestamp head. Those classes
are 80 ms time bins, not vocabulary tokens. Completion top-k would score the
wrong head and introduce an autoregressive API around a model designed to fill
all timestamp slots in one forward pass.
