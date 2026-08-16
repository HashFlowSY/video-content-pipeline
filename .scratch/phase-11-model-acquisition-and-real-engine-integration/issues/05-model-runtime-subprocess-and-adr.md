# 05 — Model runtime subprocess boundary and its ADR

**What to build:** The single new seam of the phase: a subprocess runner
for heavy MLX engines with a JSON request/response contract. The parent
serializes an engine request (model path, task payload, sampling/limits),
the child loads the model, executes, reports the result plus peak-memory
evidence (`mx.get_peak_memory()` or runtime equivalent), and exits —
returning unified memory to the OS. Child crashes and malformed responses
isolate into typed failures with retained evidence (stderr/exit captured);
no retry is automatic. Production children run with hub-offline
environment guards. Write the global ADR (Model runtime subprocess:
in-subprocess for MLX-scale engines, in-process allowed for ONNX-scale;
records the size boundary and the memory-return rationale) and add the
`Model runtime subprocess` term to the orchestration glossary.

**Blocked by:** 03
**Status:** open
**Labels:** ready-for-agent

- [ ] Protocol round-trips with a stub executable (no model, no MLX) in
      unit tests: request serialization, result parsing, peak-memory
      field required
- [ ] Child crash / garbage stdout / nonzero exit each produce a distinct
      typed failure with retained evidence; parent state stays clean
- [ ] Post-exit memory-return assertion (parent-observed child RSS gone)
- [ ] Offline guard env vars proven set in the child environment
- [ ] ADR merged and indexed in CONTEXT-MAP; glossary term added
- [ ] Full suite green within budget
