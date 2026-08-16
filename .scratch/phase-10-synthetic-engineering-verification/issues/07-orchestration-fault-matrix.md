# 07 — Run the exhaustive orchestration fault matrix

**What to build:** The phase's centerpiece failure proof, marked
`faultmatrix`. Define a micro run scenario (single Part, 2–3 stages, fake
executor is fine — the matrix targets orchestration persistence, not stage
internals) and a Golden run that counts all N `durable_io` calls via the
ticket-02 kit. Then replay the scenario N × 3 times, injecting one Fault
class (process death / ENOSPC / torn write) at the k-th call, asserting in
every cell: (a) `vcp status` classifies the wreck without mutating anything;
(b) `vcp resume` recovers to a terminal state OR the run fails into a
Minimal RunBundle — no third outcome; (c) `outputs/` is never corrupt or
partial; (d) completed units never re-execute; (e) torn state/journal tails
are repaired and the repair journaled. Exhaustiveness is structural: N is
recomputed each run, so new persistence call sites join automatically —
assert N against a recorded constant so an unreviewed new write site fails
loudly (updating the constant is the review act). Add the control-file
corruption cells (garbage/truncated control request must halt the run
safely — Phase 9 deferral; if production code mishandles it, fix it here
with version discipline). Cover the ENOSPC-during-publish and
torn-latest-pointer cells explicitly. Genuine bugs the matrix exposes are
fixed in this ticket.

**Blocked by:** 02
**Status:** done
**Labels:** ready-for-agent

- [x] Golden run enumerates N; matrix executes all N × 3 cells + control-file cells
- [x] Recorded-N assertion present (new write sites fail loudly)
- [x] All five invariants asserted in every cell
- [x] Corrupt/truncated control file halts safely (production fix if needed)
- [x] Matrix wall time fits the ≤ 5-minute full-suite budget
- [x] Suite green; ruff/mypy clean

## Comments

Done in `26e2f06` (2026-08-16). New `tests/integration/test_phase_10_fault_matrix.py`
(74 tests, marked `faultmatrix/integration/slow`). One micro run scenario
(single Part, subtitle-first, completing fake executor) drives the real
`execute_confirmed_run`/`resume_and_finalize`; the ticket-02
`DurableIoInterceptor` redirects the four `durable_io` primitives on the five
orchestration modules that import them. A golden run counts every durable write
in order (N = 23) and asserts it against `RECORDED_DURABLE_WRITE_COUNT`, so a
new persistence call site changes N and fails the golden-run test loudly
(updating the constant is the review act).

Matrix = every write position `k` in 1..N × {process death, exhausted disk,
torn write} = 69 cells, plus golden + 2 control-file + ENOSPC-during-publish +
torn-latest-pointer = 74. Every cell asserts: (a) `diagnose_run` classifies the
wreck read-only (returns a diagnosis, or raises the one controlled
`heavy_task_lock_unreadable` reason for a crash mid lock-claim) with state /
journal / lock bytes untouched; (b) the run ends in exactly one safe shape —
already terminal, resumed to a published terminal bundle, or failed in-loop
into a Minimal RunBundle — never a wedged third outcome (a survivable ENOSPC
becomes a published `failed` bundle; process death, a torn write, or a full
disk on the run's own state/journal is a genuine crash resume recovers); (c)
`outputs/` and `latest.json` are only ever absent or fully valid (atomic rename
/ atomic replace); (d) resume never re-executes a checkpointed unit; (e) a
resumed crash leaves no `run-state.json.tmp`, reads its journal back strictly,
and journals the recovery. Invariants (d)/(e) are resume-properties, so they are
vacuous and skipped on the terminal / not-resumable branch (noted in the helper).

Two genuine gaps the matrix exposed were fixed in the same commit, both keeping
the "every failure carries a machine-readable reason the CLI catches" contract:
`run_control._read_request` now also catches `UnicodeDecodeError` (a garbage
non-UTF-8 control request had leaked a bare decode error instead of
`control_request_unreadable`); and `publication` now wraps ENOSPC on the staging
writes, the post-rename directory fsync, and the latest-pointer replace into a
`PublicationError` (`staging_write_failed` / `publish_fsync_failed` /
`latest_pointer_write_failed`) instead of a bare `OSError` the CLI does not
catch. Outputs stay atomically safe either way.

Full suite 1285 green (~9s, within the ≤5-min budget); ruff + ruff format +
mypy(src) clean. Two-axis code review clean — standards: no documented-standard
violations (one judgement-call on three similar publish wrappers, kept for their
distinct reasons/rationale); spec: satisfies the ticket, the only note being the
(d)/(e) resume-scoping now made explicit in the helper.
