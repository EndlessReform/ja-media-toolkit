# Forced Alignment Plans

Start with `forced-aligner-backend-design.md` for the backend abstraction and
proof-of-value workflow.

Supporting notes:

- `srt-cleaning-batch-design.md`: current cleaned-SRT production pipeline.
- `qwen3-forced-aligner-pool-notes.md`: vLLM `/pooling` details for
  `Qwen/Qwen3-ForcedAligner-0.6B`.
- `qwen3-backend-implementation-notes.md`: MLX/vLLM implementation placement,
  shared Qwen3 helper logic, and server startup tradeoffs.
- `run-artifacts-and-serialization.md`: what core should know about durable
  records versus local workspace implementation details.

The API is intentionally not frozen yet. Treat these notes as the evaluation
track from cleaned subtitle text to backend-neutral timing evidence, then to
retiming experiments.
