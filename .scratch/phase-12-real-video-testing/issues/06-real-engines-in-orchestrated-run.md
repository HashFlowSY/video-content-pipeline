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

**Status:** ready-for-agent

- [ ] Run composition selects real adapters for an orchestrated run when capability states permit, offline adapters for the automated-test path
- [ ] pause at a stage boundary and subsequent resume work across model-runtime-subprocess stages without re-running completed stages
- [ ] cancel at a stage boundary publishes per existing failure/cancel semantics
- [ ] Hub-offline guards verified on in the real-adapter path; missing pinned asset is a typed failure
- [ ] Full engineering suite green with no real model loaded in CI
