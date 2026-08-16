# 02 — Widen plan confirmation to front-load all run choices

**What to build:** Front-loaded plan choices (source-planning term): plan
confirmation captures every run-affecting choice — subtitle track selection,
analysis audio stream, diarization candidate, ASR mode (subtitle-first, full
ASR, enhancement scope), and visual-text enablement scope — into the
immutable RunPlan, so that `vcp run` can execute non-interactively and any
missing required choice is representable as a recorded decision rather than
a prompt.

**Blocked by:** None — can start immediately.

**Status:** ready-for-agent
**Labels:** ready-for-agent

- [ ] The confirmed RunPlan schema carries every choice the sixteen per-phase
  commands currently take as interactive/CLI selections, each with explicit
  provenance (user-chosen vs. recommended-and-confirmed).
- [ ] Confirmed plans remain immutable; adding or changing a choice requires
  a new plan (existing `confirmed_plan_matches` revalidation extended).
- [ ] A plan missing a required choice is still confirmable when the choice
  is not needed by its mode; the gap is machine-detectable per stage so the
  orchestrator can surface a Run decision pause.
- [ ] Existing per-phase commands accept a confirmed plan's front-loaded
  choices without behavior change (700-test baseline stays green).
