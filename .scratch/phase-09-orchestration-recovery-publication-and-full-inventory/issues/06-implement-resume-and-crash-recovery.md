# 06 — Implement resume and crash recovery

**What to build:** The three-case `vcp resume` contract: continuing a
`paused` run (no decision), answering a Run decision pause (`--decision` must
match the recorded requirement), and Crash recovery — detecting `running`
state with a stale Heavy-task lock, discarding work past the last checkpoint,
revalidating checkpointed units by their invalidation keys, journaling a
recovery event, and continuing (ADR 0052, ADR 0053).

**Blocked by:** 04, 05

**Status:** done
**Labels:** ready-for-agent

- [x] Resume of a `paused` run continues from the next unit without
  re-running completed valid units.
- [x] Resume of a decision pause requires the matching decision string;
  mismatch or absence is an error that changes nothing.
- [x] Crash recovery is triggered only by `running` + stale lock; a live
  lock refuses resume with a clear reason.
- [x] Kill injection (process terminated mid-unit) and truncation injection
  (torn `run-state.json` temp artifacts, truncated `events.jsonl` tail)
  recover to a consistent state with at most the current unit lost.
- [x] A crash is never persisted as a state; `vcp status` reports the
  stale-running diagnosis without mutating anything.
- [x] Recovery events record what was discarded, what was revalidated, and
  why.

## Comments

Implemented in commit ae0a03d feat: implement resume and crash recovery (Phase
9 ticket 06). Acceptance criteria were checked at phase closure on the
maintainer's instruction, anchored to the current-head verification (pytest
1034 passed; ruff and mypy clean; 21 confirmed exit-gate booleans in
docs/PHASE_09_INVENTORY.json).
