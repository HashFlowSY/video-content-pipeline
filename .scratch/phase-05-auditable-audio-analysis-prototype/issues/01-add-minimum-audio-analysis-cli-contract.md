# 01 -- Add minimum audio-analysis CLI contract

**What to build:** A user can invoke `vcp analyze-audio` for a confirmed RunPlan
and retained Subtitle candidate report and receive an immutable, machine-readable
Audio analysis report. When no approved model is present, the report explicitly
states `model_acquisition_required` without choosing, downloading, or invoking a
model.

**Blocked by:** None -- can start immediately.

**Status:** ready-for-agent
**Labels:** ready-for-agent

- [ ] The CLI accepts only the confirmed RunPlan and retained Subtitle candidate report required by Phase 5, and retains a new Audio analysis workspace and report identity.
- [ ] Missing, unavailable, credential-gated, or otherwise ineligible capabilities report their status without network access, model acquisition, model execution, ASR, or `outputs/` publication.
- [ ] The JSON CLI contract proves that existing RunPlans and Phase 4 source/readable artifacts remain unchanged.
