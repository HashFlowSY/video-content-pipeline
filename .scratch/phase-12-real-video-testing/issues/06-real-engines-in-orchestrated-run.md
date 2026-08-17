# 06 — Real engines invoked by the orchestrated run

**What to build:** `vcp run` on a confirmed RunPlan drives the real engines
for every acquired capability (asr_primary, asr_review, forced_alignment,
vad, diarization, ocr_primary, text_semantics) instead of stopping at the
offline path. The controlled offline adapters remain the automated-test
path exactly as ADR 0037 prescribes — real adapters run beside them, and
run composition selects real adapters when the orchestrated run's
capability states permit. pause / resume / cancel behave correctly across
Model runtime subprocess stages (ADR 0053, ADR 0055): a control request
observed at a stage boundary pauses cleanly, and resume continues without
re-running completed stages. Hub-offline guards stay forced on; a missing
pinned asset is a typed failure, never a download.

The automated suite never loads a real model: the new observable point is
run composition's adapter selection (a real orchestrated run selects real
adapters; the suite's runs select offline adapters). The real engines
themselves are verified by run #1's real evidence (ticket 08), not by CI.

**Blocked by:** None — can start immediately.

**Status:** done

- [x] Run composition selects real adapters for an orchestrated run when capability states permit, offline adapters for the automated-test path
- [x] pause at a stage boundary and subsequent resume work across model-runtime-subprocess stages without re-running completed stages
- [x] cancel at a stage boundary publishes per existing failure/cancel semantics
- [x] Hub-offline guards verified on in the real-adapter path; missing pinned asset is a typed failure
- [x] Full engineering suite green with no real model loaded in CI

## Closure note (2026-08-17)

Landed the **CI-provable core** of the seam, per the maintainer's scope decision
(the full real inference bodies + registry promotion + calibration land against
run #1 / ticket 08, where real output is proven — not blind in CI):

- **Adapter selection** (`run_composition.select_adapter_profile` /
  `AdapterProfile`): a pure function of registry metadata — a capability runs
  real iff the shared eligibility gate grades a schema-2 candidate `eligible`
  *and* it carries no `controlled_adapter` fixture (ADR 0037's automated-test
  path). No model is ever loaded to decide. `build_run_composition` computes the
  profile and hands each model-bearing stage the subset of its capabilities
  graded real; the automated suite always selects offline.
- **Engine-provider seam** threaded into all five stage functions
  (`analyze_audio`, `transcribe`, `enhance`, `analyze_text`, `run_visual_text`):
  an optional `real_engines` param, default `None` → the controlled offline path
  byte-identical to before. When set, the stage delegates to
  `real_engine_adapter.dispatch_real_stage`, the reachable real-branch **entry**:
  it verifies each selected capability's pinned asset from disk through that
  capability's own engine loader (typed `*_asset_unavailable` / `*_asset_mismatch`,
  local-only, never a download; the eventual load runs through the Model runtime
  subprocess, which forces the hub-offline guards), then fails closed with a
  typed `real_engine_execution_deferred` so a real run never silently falls back
  to the offline adapter.
- **pause / resume / cancel across subprocess-model stages**: proven at the run
  loop's boundary seam with a controlled composition standing in for the
  subprocess stages — pause after a completed model stage, resume adopts it and
  never re-runs it, cancel publishes.

**Discovered and deferred to run #1 (ticket 07/08):** the real
`models/registry.json` candidates are deliberately un-promoted (no
`resource_estimate`), so the eligibility gate grades every one `unsupported` and
no run selects real today; promotion (from `device-baselines.json`), the model-
specific calibration profiles (ADR 0029/0031/0056), and the per-stage
engine-output → immutable-report / `stage_execution` bridges are completed against
run #1's real evidence.

Suite 1673 (+26). New: `real_engine_adapter.py`,
`tests/unit/test_phase_12_adapter_selection.py`,
`tests/unit/test_phase_12_real_engine_adapter.py`,
`tests/integration/test_phase_12_subprocess_stage_control.py`.
