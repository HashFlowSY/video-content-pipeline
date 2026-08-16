# 03 — Implement the single-writer run state and events journal

**What to build:** The Run state document (`run-state.json`, atomic replace)
and Run events journal (`events.jsonl`, append-only) under the single-writer
rule (ADR 0053), carrying the exact plan §12 state machine — so that a run's
status, stage units, adopted outputs, invalidation keys, and required
decisions are always consistent on disk and every transition is audited.

**Blocked by:** 01

**Status:** done
**Labels:** ready-for-agent

- [x] `run-state.json` updates are atomic (temp-then-rename in the run
  directory); a reader never observes a torn document.
- [x] `events.jsonl` is append-only; every state transition, control-request
  observation, decision pause, and recovery writes an event.
- [x] The state machine implements exactly
  `planned -> queued -> running -> complete | complete_with_warnings |
  incomplete | failed | cancelled` and `running -> pausing -> paused ->
  running`; no other transition is representable.
- [x] Only the run process writes either file; command-side code paths have
  no write API against them.
- [x] Schema versioning and deterministic serialization follow the existing
  report conventions (`sort_keys`, explicit `schema_version`).

## Comments

Implemented in commit 2663e70 feat: implement single-writer run state and
events journal (Phase 9 ticket 03). Acceptance criteria were checked at phase
closure on the maintainer's instruction, anchored to the current-head
verification (pytest 1034 passed; ruff and mypy clean; 21 confirmed exit-gate
booleans in docs/PHASE_09_INVENTORY.json).
