# 01 -- Add minimum audio-analysis CLI contract

**What to build:** A user can invoke `vcp analyze-audio` for a confirmed RunPlan
and retained Subtitle candidate report and receive an immutable, machine-readable
Audio analysis report. When no approved model is present, the report explicitly
states `model_acquisition_required` without choosing, downloading, or invoking a
model.

**Blocked by:** None -- can start immediately.

**Status:** resolved
**Labels:** ready-for-agent

- [x] The CLI accepts only the confirmed RunPlan and retained Subtitle candidate report required by Phase 5, and retains a new Audio analysis workspace and report identity.
- [x] Missing, unavailable, credential-gated, or otherwise ineligible capabilities report their status without network access, model acquisition, model execution, ASR, or `outputs/` publication.
- [x] The JSON CLI contract proves that existing RunPlans and Phase 4 source/readable artifacts remain unchanged.

## Comments

2026-08-11: Rechecked at `5c7baa4`. `vcp analyze-audio` creates an immutable
report workspace, validates the confirmed RunPlan and retained subtitle report,
and reports unavailable capabilities without acquisition or execution. The
current full verification passed: 150 tests, Ruff, formatter check, and strict
Mypy. Controlled fixtures only; no model, network, user-media, or `outputs/`
action occurred.
