# 10 — Build the five-branch CLI acceptance layer

**What to build:** The named acceptance layer the plan's CLI list demands:
`tests/acceptance/test_phase_10_cli_acceptance.py` (marked `integration`),
driving the orchestration surface end to end over the five fixture branches
with ticket 08's real composition. Per branch: `plan → run → status →
verify → inventory` green. Across branches (not per-branch, for budget):
`pause`/`resume` exercised once at a real unit boundary, `cancel` once,
and `vcp improve` once — derived from a bundle actually published by the
enhancement-eligible branch, proving carry-forward against real published
bytes. Re-assert the standing guarantees at this layer: non-publication
commands never write `outputs/`; failed runs never advance the latest
pointer; published bundles hash-verify. The 16 expert commands are
explicitly out of scope (their per-phase tests remain their contract).

**Blocked by:** 03, 08
**Status:** open
**Labels:** ready-for-agent

- [ ] All five branches complete plan/run/status/verify/inventory
- [ ] pause/resume, cancel, and improve each exercised against real runs
- [ ] improve carry-forward verified against the published source bundle
- [ ] Standing guarantees asserted; no expert-command re-acceptance
- [ ] Full suite green within the ≤ 5-minute budget

## Comments
