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
**Status:** open
**Labels:** ready-for-agent

- [ ] Golden run enumerates N; matrix executes all N × 3 cells + control-file cells
- [ ] Recorded-N assertion present (new write sites fail loudly)
- [ ] All five invariants asserted in every cell
- [ ] Corrupt/truncated control file halts safely (production fix if needed)
- [ ] Matrix wall time fits the ≤ 5-minute full-suite budget
- [ ] Suite green; ruff/mypy clean

## Comments
