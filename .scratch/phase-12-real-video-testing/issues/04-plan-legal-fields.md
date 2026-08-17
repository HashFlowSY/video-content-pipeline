# 04 — Plan legal fields: peak memory and model status

**What to build:** Before confirming any real test, the maintainer sees all
four fields the execution plan requires of a pre-run plan: estimated time,
peak memory, disk, and model status. The PlanReport gains (a) a peak-memory
estimate whose basis is the recorded Phase 11 device-baseline measurements
per capability — an evidence-backed estimate in the established
phase-bounded-estimate style, never an invented number; and (b)
per-capability model status derived from the model registry (the same
capability states the stages consume), so a missing or ineligible model is
visible at plan time instead of failing mid-run.

**Blocked by:** None — can start immediately.

**Status:** done

- [x] Plan output shows a peak-memory estimate with its stated evidence basis
- [x] Plan output shows per-capability model status sourced from the registry
- [x] A capability whose model is missing/ineligible surfaces that status at plan time
- [x] Existing plan fields (three-point time estimate, disk headroom) unchanged
- [x] Assertions run at the CLI command boundary (`vcp plan` output), following existing plan-report test prior art

**Done (`1758799`):** `PlanReport` gained `peak_memory_estimate`
(device-baseline-backed; largest single-capability peak since stages load one
model at a time; confidence derived from the baselines, not asserted) and
`model_statuses` (per-capability state via the shared eligibility gate). Both
advisory — excluded from `confirmed_plan_matches`/`RunPlan` so post-confirm
model acquisition cannot cause drift. `create_plan_report` takes an optional
`project_root`; the cli report paths thread it through. Suite 1645.
