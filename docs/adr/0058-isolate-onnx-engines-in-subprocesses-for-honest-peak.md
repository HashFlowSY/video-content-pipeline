# Isolate ONNX-scale engines in subprocesses for honest orchestrated-run peak

Every real model engine the orchestrated `vcp run` invokes — the ONNX-scale ones
(silero VAD, sherpa-onnx diarization, RapidOCR) as well as the MLX-scale ones —
runs in its own Model runtime subprocess (the ADR 0055 seam). This **supersedes
the size boundary in ADR 0055**, which ran ONNX-scale engines in-process because
they are small enough not to need memory returned to the OS between stages.

The memory-return rationale is unchanged and still holds; this adds a second,
independent rationale that the in-process choice cannot satisfy in the
orchestrated run: **honest, baseline-comparable per-capability peak memory**. The
Phase 11 device baselines (`docs/phase-11-prototypes/device-baselines.json`) were
each measured in a fresh, one-capability-per-process prototype run via
`resource.getrusage(RUSAGE_SELF).ru_maxrss`, a monotonic high-water mark. In a
single orchestrated `vcp run` process every stage would share that counter, so an
in-process ONNX engine's measured peak would be the process-cumulative high-water
mark — not comparable to its per-capability baseline, and large enough to trip the
per-stage resource-envelope gate with false "exceeded" failures. Running each ONNX
engine in its own child restores the fresh-process measurement the baselines
assume: the child's `ru_maxrss` is that capability's own peak, the child's exit
makes `resident_bytes: 0` truthful, and `stage_execution` records a real
`peak_memory_bytes` that the 12 GiB envelope check can trust.

The boundary stays the same seam (ADR 0055): a JSON request/response over stdin/
stdout, hub-offline guards forced on the child, typed crash/timeout isolation, and
no model or network in the parent. The child runs the capability's existing
`analyze_derivative_*` body and returns the already-report-shaped evidence plus its
peak; the parent re-derives any cross-stage data it needs (VAD speech-run chunks
for alignment) with pure functions and applies the shared ADR 0030/0031 gate
in-process. The controlled offline adapters (ADR 0037) are unaffected: the
automated suite loads no model and crosses no boundary.

## Considered Options

- Subprocess-isolate every real engine, ONNX-scale included, reusing the ADR 0055
  seam: accepted because it is the only way the orchestrated run can record a
  per-capability peak comparable to the device baselines and to the resource
  envelope, and it composes with the existing memory-return and hub-offline
  guarantees at negligible cost (the ONNX children are short-lived and small).
- Keep ONNX engines in-process and measure the whole-run process peak once against
  the 12 GiB envelope, recording in-process stages' peak as a labelled
  process-cumulative figure: rejected because the per-stage `stage_execution`
  envelope gate would then compare a cumulative high-water mark against a
  per-capability baseline and fail otherwise-good stages, and the recorded
  per-capability peak would overstate each engine's real footprint.
- Reset or sample memory in-process to attribute a per-capability peak without a
  subprocess: rejected because `ru_maxrss` is monotonic and cannot be reset, and a
  sampling probe adds a concurrency and accuracy burden a fresh child avoids
  outright.
