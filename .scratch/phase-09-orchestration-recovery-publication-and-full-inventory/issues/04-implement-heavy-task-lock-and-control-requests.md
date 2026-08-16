# 04 — Implement the heavy-task lock and control requests

**What to build:** The Heavy-task lock (holder run id, process id, process
start time; stale when the holder is dead) serializing heavy runs, and
Control requests: `vcp pause` / `vcp cancel` write request files that the run
process observes at stage-unit boundaries, journals, and executes —
`running -> pausing -> paused` with clean process exit, or the cancel path
that stops later stages while still publishing existing results (ADR 0053,
ADR 0032).

**Blocked by:** 03

**Status:** ready-for-agent
**Labels:** ready-for-agent

- [ ] A second heavy run while the lock is held fails fast with a clear
  reason; `queued` exists only as the transient lock-wait state.
- [ ] A lock whose holder process is dead (pid + start-time check) is
  detected as stale and reported as such.
- [ ] Pause takes effect exactly at the next stage-unit boundary; the run
  process exits cleanly in `paused` with state and journal flushed.
- [ ] Cancel stops subsequent stages and hands off to publication of
  already-completed results.
- [ ] Control request files, their observation, and their outcomes are all
  journaled events; an unobserved stale request cannot corrupt state.
