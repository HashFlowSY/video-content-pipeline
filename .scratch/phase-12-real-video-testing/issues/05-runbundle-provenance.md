# 05 — RunBundle processing-report provenance

**What to build:** A published RunBundle's processing report tells the
truth about what produced it. Replace the conservative empty-inputs stub so
the report carries: every model actually used (name, revision, sha256,
size, purpose — from the model registry entries of the engines the run
selected), tools, environment, run parameters, and measured resource usage
(peak memory from the model-runtime-subprocess evidence, stage durations,
disk delta). This is what binds a Coverage-ledger entry to the model stack
that produced the outputs — the phase's acceptance item "输出文件、证据引用
和处理清单完整" cannot pass while these sections are empty.

**Blocked by:** None — can start immediately.

**Status:** done (`eb1eb5b`, 2026-08-17)

- [x] An offline golden run publishes a processing report with non-empty models, tools, environment, parameters, and resource-usage sections
- [x] Model entries carry name, revision, sha256, size, and purpose consistent with the registry
- [x] Resource usage reflects measured values (peak memory, durations, disk delta), not placeholders
- [x] A run that used no model for a stage honestly omits it (no padding)
- [x] Assertions run at the CLI command boundary against published RunBundle contents (Phase 9/10 golden-run prior art)

**Closure note.** `_gather_report_inputs` (run_composition.py) now reads the
completed stages' `stage_execution` records through one seam
(`_completed_executions`) and describes each executed engine from
`models/registry.json`; tools come from the confirmed plan, environment from the
running interpreter + `uv.lock`, parameters from the front-loaded run choices,
and resource usage is measured (peak from the recorded `resource_measurement`,
elapsed from the run journal, disk delta from the run-owned `work/` tree). Two
CLI-boundary acceptance tests in `test_phase_10_cli_acceptance.py` cover the
completed and no-model cases; suite 1647, mypy/ruff clean.

Boundary with ticket 06: audio analysis is the only orchestrated stage that
records executed-model evidence today, so the ASR/text/visual executed-model and
subprocess-peak figures flow in through the same seam once ticket 06 invokes the
real engines — no further change here. Per-stage durations are not recorded by
any stage; whole-run elapsed is the honest measurement available.
