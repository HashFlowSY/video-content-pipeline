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
**Status:** done
**Labels:** ready-for-agent

- [x] Protocol round-trips with a stub executable (no model, no MLX) in
      unit tests: request serialization, result parsing, peak-memory
      field required
- [x] Child crash / garbage stdout / nonzero exit each produce a distinct
      typed failure with retained evidence; parent state stays clean
- [x] Post-exit memory-return assertion (parent-observed child RSS gone)
- [x] Offline guard env vars proven set in the child environment
- [x] ADR merged and indexed in CONTEXT-MAP; glossary term added
- [x] Full suite green within budget

**Closed:** `src/video_content_pipeline/model_runtime.py` (parent
`run_engine_subprocess` with forced hub-offline guards + Popen pid capture and
four distinct typed failures: `engine_child_crashed`/`engine_child_exit_nonzero`
/`engine_response_invalid`/`engine_timeout`; child helpers `read_request`/
`write_result`/`execute_child`/`process_peak_rss_bytes`). ADR 0055 (MLX-scale in
subprocess, ONNX-scale in-process; memory-return rationale) indexed in
CONTEXT-MAP global ADR index + orchestration relevant ADRs; `Model runtime
subprocess` added to the orchestration owner index and CONTEXT.md glossary, and
to `POST_MIGRATION_TERMS` in the document-layout contract. Tests:
`tests/unit/test_model_runtime.py` (15). Suite 1393 green in ~26s; mypy+ruff
clean.
