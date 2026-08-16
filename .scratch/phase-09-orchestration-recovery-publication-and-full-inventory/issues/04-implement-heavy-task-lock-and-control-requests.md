# 04 — Implement the heavy-task lock and control requests

**What to build:** The Heavy-task lock (holder run id, process id, process
start time; stale when the holder is dead) serializing heavy runs, and
Control requests: `vcp pause` / `vcp cancel` write request files that the run
process observes at stage-unit boundaries, journals, and executes —
`running -> pausing -> paused` with clean process exit, or the cancel path
that stops later stages while still publishing existing results (ADR 0053,
ADR 0032).

**Blocked by:** 03

**Status:** done
**Labels:** ready-for-agent

- [x] A second heavy run while the lock is held fails fast with a clear
  reason; `queued` exists only as the transient lock-wait state.
- [x] A lock whose holder process is dead (pid + start-time check) is
  detected as stale and reported as such.
- [x] Pause takes effect exactly at the next stage-unit boundary; the run
  process exits cleanly in `paused` with state and journal flushed.
- [x] Cancel stops subsequent stages and hands off to publication of
  already-completed results.
- [x] Control request files, their observation, and their outcomes are all
  journaled events; an unobserved stale request cannot corrupt state.

## Comments

Implemented in commit dabd69c feat: implement heavy-task lock and control
requests (Phase 9 ticket 04). Acceptance criteria were checked at phase closure
on the maintainer's instruction, anchored to the current-head verification
(pytest 1034 passed; ruff and mypy clean; 21 confirmed exit-gate booleans in
docs/PHASE_09_INVENTORY.json).
