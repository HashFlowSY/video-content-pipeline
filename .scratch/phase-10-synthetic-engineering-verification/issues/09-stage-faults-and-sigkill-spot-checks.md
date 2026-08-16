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
**Status:** done
**Labels:** ready-for-agent

- [x] Every DAG stage has ≥ 1 exception-injection test with stage-scoped assertions
      — `test_stage_exception_fails_run_and_publishes` is parametrized over all seven
      stages (transcription vs enhancement plans; visual_text with its front-loaded
      scope) and asserts the abort is anchored to the target: predecessors recorded
      COMPLETED, the target left no checkpoint, and a published verifiable `failed`
      bundle with `failure_reason` carried through
- [x] ≥ 2 real SIGKILL subprocess tests (mid-stage, mid-publish) recover via CLI —
      `test_sigkill_mid_stage_recovers_via_cli` and `..._mid_publish_...` spawn
      `tests/support/kill_harness` as a subprocess, `os.kill(SIGKILL)` it, then drive
      the real `cli.main(["status"…])` / `cli.main(["resume"…])`; the live CLI
      diagnoses `crashed`/`stale_running` and resume steals the dead process's lock
      and publishes a verified bundle
- [x] blocked-isolation and failed-bundle behaviors asserted per stage class —
      `test_per_part_failure_isolates_downstream` (per-Part FAILED → own downstream
      BLOCKED, sibling Part COMPLETED, run `incomplete`) and
      `test_collection_failure_fails_whole_run` (collection FAILED → all Parts
      BLOCKED, run `failed`); both publish a verifiable bundle
- [x] Suite green within budget — full suite 1305 passed in ~11s; ruff + mypy(src) clean

## Comments

Delivered as `c30298c` on top of ticket 08. Two files plus one support module.

**Tier 1 — `tests/integration/test_phase_10_stage_faults.py`** drives the real
`execute_confirmed_run`, real `durable_io`, and the real publication path with only
the executor and gathered report inputs controlled (no model/media/network). It
injects at the executor seam the production `RunComposition` fills, and pins the
four stage-scoped consequences: a raised exception at *any* of the seven stages
aborts the whole run into a published `failed` bundle (anchored to the target stage
by predecessor/target checkpoint state); a per-Part `FAILED` collapses only that
Part's downstream to `blocked` and classifies `incomplete`; a collection-stage
`FAILED` blocks every Part and classifies `failed`; a recorded gate `warning`
classifies `complete_with_warnings`. Covering all seven stages needs both a
transcription plan and an enhancement plan (mutually exclusive), plus visual_text's
front-loaded scope choice — otherwise the run decision-pauses before the DAG runs.

**Tier 2 — `tests/support/kill_harness.py` + `tests/integration/test_phase_10_
sigkill_spot_checks.py`** are the real power-loss spot checks (marked `slow`). The
harness is executed as a separate process (`python -m tests.support.kill_harness`),
runs the real `execute_confirmed_run` over real `durable_io` and the real
`SystemProcessProbe` so the on-disk state and orphaned heavy-task lock carry the
child's real identity, and blocks (writing a `ready` marker, then sleeping) at one
of two deterministic points: mid-stage inside the executor, or in the finalization
window — wrapping `classify_completed_run`, the last call before the terminal
transition, so the state is still `running` when the kill lands (the only
publish-window crash resume can still drive to a published bundle, per ticket 07's
boundary). The test SIGKILLs the child, confirms `returncode == -SIGKILL`, then
recovers through the real CLI: `vcp status --run` reports `crashed`/`stale_running`
without mutating state, and `vcp resume --run` steals the dead lock and publishes a
verified bundle, adopting all durably-checkpointed units (mid-stage re-executes
only the interrupted+later units; mid-publish re-executes none). This extends the
Phase 9 CLI kill test from wedged-state simulation to a genuinely killed process.

No production code changed — the run loop already produced every asserted behavior.
The harness persists the plan to `plans/<id>/run-plan.json` so the resume CLI can
load it; the harness's one bare module-attribute rebind of `classify_completed_run`
is documented as safe (throwaway SIGKILLed subprocess, never restored, cannot leak).

Two-axis code review (Standards + Spec) ran before this closure. Applied: a
reason-carrying `_InjectedStageFault(RuntimeError)` replacing a grafted `.reason`
(matching the repo's exception idiom); stage-anchored predecessor/target assertions
so the per-stage exception test is no longer near-vacuous (Spec finding 1); and the
CLI-level recovery refactor above so status/resume run through the real
`cli.main`, faithfully "extending the Phase 9 CLI kill test" as the ticket asks.
