# 09 — Representative per-stage faults and real SIGKILL spot checks

**What to build:** The two verification tiers that complement ticket 07's
deterministic matrix. (1) Per-stage representative injection: for every
stage in the DAG, inject at least one raised exception through its
adapter/executor seam (ticket 08's real composition where practical) and
prove the stage-scoped consequences: failed bundle published for
collection-stage failure, per-Part failure isolates downstream units as
`blocked`, gate warnings propagate to run classification. (2) Real
power-loss spot checks (marked `slow`): subprocess-driven `vcp run` killed
with SIGKILL at 2–3 real moments (mid-stage, mid-publish), then CLI-level
`vcp status` diagnoses crashed and `vcp resume` recovers to terminal +
published — extending the Phase 9 CLI kill test from wedged-state
simulation to a genuinely killed process.

**Blocked by:** 02, 08
**Status:** open
**Labels:** ready-for-agent

- [ ] Every DAG stage has ≥ 1 exception-injection test with stage-scoped assertions
- [ ] ≥ 2 real SIGKILL subprocess tests (mid-stage, mid-publish) recover via CLI
- [ ] blocked-isolation and failed-bundle behaviors asserted per stage class
- [ ] Suite green within budget

## Comments
