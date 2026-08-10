# 06 -- Prove the subtitle CLI contract offline

**What to build:** Maintainers have repeatable synthetic proof that the full
`vcp subtitles` contract produces correct observable states and retains its
evidence without external side effects.

**Blocked by:** 01 -- Process one verified subtitle track end to end; 02 -- Produce common-format readable subtitles; 03 -- Resolve ambiguous subtitle-track selection explicitly; 04 -- Preserve bounded subtitle-processing failures; 05 -- Report partial collections and ASR handoff.

**Status:** resolved
**Labels:** ready-for-agent

- [x] Controlled CLI tests cover successful source/readable candidates,
  selection pause/resume, failure retention, partial collections, and
  ASR-required handoff from confirmed synthetic RunPlans.
- [x] Fixture-backed proof uses only retained project-owned synthetic media and
  the permitted bounded FFmpeg path; all other tool behavior is controlled.
- [x] The project environment gate, full tests, lint, format, and type checks
  pass without user media, network, model, dependency, or paid API access.

## Comments

2026-08-10: Added a direct offline CLI contract test for the all-unavailable
subtitle state. It proves the ASR-planning handoff is retained per Part while
no extraction, source candidate, output, or RunBundle side effect occurs. The
complete Phase 4 suite and static checks pass under the project environment
gate.
