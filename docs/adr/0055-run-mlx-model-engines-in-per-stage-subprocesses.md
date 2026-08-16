# Run MLX-scale model engines in per-stage subprocesses

Every MLX-scale model engine (mlx-audio ASR and forced alignment, mlx-whisper
review ASR, mlx-lm text semantics) executes in its own subprocess through a
single transport seam, the Model runtime subprocess. The parent serializes an
engine request — model path, task payload, and sampling/limits — to the child's
stdin; the child loads the model, runs the stage, measures peak memory, writes a
JSON response carrying the engine output plus a required `peak_memory_bytes`
evidence field, and exits, returning its unified memory to the OS. The parent
inherits its environment with the hub-offline guards
(`HF_HUB_OFFLINE`, `TRANSFORMERS_OFFLINE`, `HF_HUB_DISABLE_TELEMETRY`,
`HF_HUB_DISABLE_IMPLICIT_TOKEN`) forced on top, so a production child can never
reach the hub, emit telemetry, or pick up an implicit token. The seam is pure
transport: no model, MLX, or network lives in it, so its protocol is verified
against a stub executable.

Any abnormal outcome isolates into a distinct typed failure that retains the
child's exit code and truncated stderr/stdout as evidence and never retries: a
child killed by signal (`engine_child_crashed`), a nonzero exit
(`engine_child_exit_nonzero`), a stdout that is not the required JSON shape
(`engine_response_invalid`, which also covers a missing or ill-typed
`peak_memory_bytes`), and an exceeded time budget (`engine_timeout`). The parent
writes nothing to disk, so a failed stage leaves parent state clean for its
caller to decide what to do.

The size boundary is explicit: MLX-scale engines cross the subprocess boundary
because unified memory on the 16 GiB test machine must be returned to the OS
before the next model loads, and in-process framework unloading cannot be
trusted to do that. ONNX-scale models (vad, diarization, ocr) are small enough
to run in-process and do not cross the boundary. Per-stage measured peak memory
is recorded as evidence so plan estimates converge on reality against the
12 GiB envelope.

## Considered Options

- Per-stage subprocess with a JSON request/response contract, memory returned
  on exit, offline guards forced on the child, and typed crash isolation:
  accepted because process exit is the only mechanism that reliably returns
  unified memory on Apple silicon; a crashed or hung engine cannot corrupt the
  parent because the boundary is a pipe, not shared state; and the wire is
  stub-testable so the engineering gate stays fast, offline, and model-free.
- In-process model loading with explicit unload/`del`/GC between stages:
  rejected because MLX and its dependencies give no dependable release of
  unified memory within a live process, so the next model would load under the
  previous stage's residual footprint — exactly the pressure the 12 GiB
  envelope exists to avoid.
- One long-lived model-server subprocess serving many stages over IPC:
  rejected because it defeats the memory-return goal (weights stay resident
  across stages) and adds a second lifecycle and failure domain beyond what a
  single-CLI first version needs.
- Subprocessing every model, ONNX-scale included: rejected as needless
  overhead; the small ONNX engines return their memory in-process, and the ADR
  records the size boundary so the distinction is deliberate, not incidental.
- Automatic retry on a child failure inside the seam: rejected because a retry
  policy is a stage/orchestration decision that needs the retained evidence to
  make; the seam surfaces a typed failure and leaves the choice to its caller.
