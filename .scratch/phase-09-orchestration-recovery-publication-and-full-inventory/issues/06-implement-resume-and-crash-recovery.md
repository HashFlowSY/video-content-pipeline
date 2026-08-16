# 06 — Implement resume and crash recovery

**What to build:** The three-case `vcp resume` contract: continuing a
`paused` run (no decision), answering a Run decision pause (`--decision` must
match the recorded requirement), and Crash recovery — detecting `running`
state with a stale Heavy-task lock, discarding work past the last checkpoint,
revalidating checkpointed units by their invalidation keys, journaling a
recovery event, and continuing (ADR 0052, ADR 0053).

**Blocked by:** 04, 05

**Status:** ready-for-agent
**Labels:** ready-for-agent

- [ ] Resume of a `paused` run continues from the next unit without
  re-running completed valid units.
- [ ] Resume of a decision pause requires the matching decision string;
  mismatch or absence is an error that changes nothing.
- [ ] Crash recovery is triggered only by `running` + stale lock; a live
  lock refuses resume with a clear reason.
- [ ] Kill injection (process terminated mid-unit) and truncation injection
  (torn `run-state.json` temp artifacts, truncated `events.jsonl` tail)
  recover to a consistent state with at most the current unit lost.
- [ ] A crash is never persisted as a state; `vcp status` reports the
  stale-running diagnosis without mutating anything.
- [ ] Recovery events record what was discarded, what was revalidated, and
  why.
