# Distribution Review Server Runbook

Status: source-controlled handoff companion to
[`distribution-review-and-consensus.md`](distribution-review-and-consensus.md).

Commands under **Available now** exist on this branch. Commands under **Target
interface** define what the next implementation must make runnable; they are not
working commands yet.

## Available Now: Start And Check The Aligner

Remote infrastructure remains user-operated. From the repository checkout on the
inference server:

```sh
cd deploy/qwen3-forced-aligner-vllm
./scripts/start-compose.sh
./scripts/smoke-health.sh
docker compose ps
```

The wrapper loads the deployment's machine-local `.env` plus the selected data
configuration according to the deployment README. Do not print either secret file.

If only adapter code changed and vLLM should remain loaded:

```sh
cd deploy/qwen3-forced-aligner-vllm
./scripts/restart-adapter.sh
./scripts/smoke-health.sh
```

Follow logs when startup or a campaign request fails:

```sh
cd deploy/qwen3-forced-aligner-vllm
docker compose logs -f adapter
docker compose logs -f vllm
```

The existing crop-position campaign can be reproduced once the slice and target
artifacts are present on the server:

```sh
cd envs/inference
uv run qwen3-retime-case edge \
  ../../research/forced-alignment-retiming/output/corpus-slice-2026-08-22/slice.json \
  --reviewed-targets ../../research/forced-alignment-retiming/edge-dialogue-targets.json \
  --edge-stage run \
  --concurrency 32
```

The command resolves the configured forced-alignment backend when `--base-url` is
omitted.

## Available Now: Laptop Port Forward

The review workbench does not exist yet, but its reserved loopback port is 8765.
Once its server process is running, establish the tunnel from the laptop:

```sh
ssh -N -L 8765:127.0.0.1:8765 <inference-server>
```

Then open `http://127.0.0.1:8765`. The placeholder is supplied by the user rather
than committed as a private hostname.

## Target Interface: Distribution Campaign

Extend the existing private `qwen3-retime-case` executable with one general
`distribution` operation and explicit stages. This avoids a public `ja-media`
command or a dedicated command for every campaign.

Dry-run the input-pinned representative sample and projected storage:

```sh
cd envs/inference
uv run qwen3-retime-case distribution \
  ../../research/forced-alignment-retiming/output/corpus-slice-2026-08-22/slice.json \
  --distribution-stage plan \
  --sample representative \
  --sample-count 120 \
  --placements 5 \
  --window-s 60 \
  --capture full,top64,histogram
```

Run or resume the approved plan:

```sh
cd envs/inference
uv run qwen3-retime-case distribution \
  ../../research/forced-alignment-retiming/output/corpus-slice-2026-08-22/slice.json \
  --distribution-stage run \
  --campaign-manifest ../../research/forced-alignment-retiming/output/corpus-slice-2026-08-22/distribution-campaign/manifest.json \
  --concurrency 32
```

Render or repair tables and static plots without repeating inference:

```sh
cd envs/inference
uv run qwen3-retime-case distribution \
  ../../research/forced-alignment-retiming/output/corpus-slice-2026-08-22/slice.json \
  --distribution-stage render \
  --campaign-manifest ../../research/forced-alignment-retiming/output/corpus-slice-2026-08-22/distribution-campaign/manifest.json
```

The implemented command must print the campaign directory, manifest, endpoint-row
count, projected or written fp16 bytes, completed request count, and next command.
`run` must resume by stable request ID and must not overwrite a different input
fingerprint.

## Target Interface: Review Workbench

Serve one frozen campaign from the inference server:

```sh
cd envs/inference
uv run qwen3-distribution-review serve \
  ../../research/forced-alignment-retiming/output/corpus-slice-2026-08-22/distribution-campaign \
  --host 127.0.0.1 \
  --port 8765 \
  --source-cache-items 2
```

The process must refuse a non-loopback bind unless a later, separately approved
deployment design adds authentication. Startup output must print the campaign
fingerprint, review-row count, human judgment path, clip-cache bounds, and local
URL without printing storage configuration or credentials.

Run Luna fanout only after the human-first review set is frozen:

```sh
cd envs/inference
uv run --env-file ../../.env qwen3-distribution-review fanout \
  ../../research/forced-alignment-retiming/output/corpus-slice-2026-08-22/distribution-campaign \
  --provider openai \
  --model gpt-5.6-luna \
  --responses-per-cue 3 \
  --resume
```

Replay registered aggregators without calling Qwen again:

```sh
cd envs/inference
uv run qwen3-distribution-review aggregate \
  ../../research/forced-alignment-retiming/output/corpus-slice-2026-08-22/distribution-campaign \
  --methods current,medoid,dense-cluster,arithmetic-pool,log-pool,peak-cluster
```

The executable name and packaging location may change during implementation, but
the plan/run/render/serve/fanout/aggregate operations and their artifact boundaries
are part of the proposed handoff interface.
