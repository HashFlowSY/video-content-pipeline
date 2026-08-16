# Phase 9 Completion Report

## Status

Phase 9, orchestration, recovery, publication, and full inventory, is
completed and verified in the project-local offline environment. Per the
phase exit gate, this is an engineering pass only: domain quality is not
verified because no real model was downloaded or invoked and no real media
was processed; publication was exercised only inside synthetic test project
roots, and the repository's own `outputs/` holds no published bundle. The
project remains in engineering development; `real_world_testing` and
`production_validated` are both `false`.

## Delivered Scope

- Collection run identity and layout: a collection-level `source-id` from
  ordered Part content hashes, `run-id` from run start time + immutable plan
  id + configuration hash, run-owned state under `work/<source-id>/<run-id>/`
  with a staging area in final RunBundle layout, and published bundles under
  `outputs/<source-id>/<run-id>/` that are never overwritten by construction.
- Front-loaded plan choices: plan confirmation captures every run-affecting
  choice (subtitle track, analysis audio stream, diarization candidate, ASR
  mode, visual-text scope) with provenance into the immutable RunPlan, so
  `vcp run` executes non-interactively and any gap is machine-detectable per
  stage.
- Single-writer run state: an atomically replaced `run-state.json` and
  append-only `events.jsonl`, written only by the run process, encoding the
  exact plan §12 state machine; every transition, control-request
  observation, decision pause, and recovery is a journaled event
  (ADR 0053).
- Heavy-task lock and control requests: heavy runs serialize behind a lock
  recording run id, pid, and process start time (stale when the holder is
  dead); `queued` exists only as the transient lock wait; `vcp pause` and
  `vcp cancel` write control request files observed at stage-unit boundaries,
  with pause exiting cleanly in `paused` and cancel handing off to
  publication of existing results (ADR 0032, ADR 0053).
- Stage DAG with invalidation keys: mode-driven `(stage, Part)` stage units
  in topological order, each guarded by a Stage invalidation key (input
  hashes + stage-scoped config subset + manual Stage version), checkpointed
  only at completed unit boundaries, with Run-scoped adoption that never
  scavenges manual workspaces and per-Part failure isolation (ADR 0052).
- Resume and crash recovery: one `vcp resume` contract for paused runs,
  decision pauses (matching-decision gate; mismatch changes nothing), and
  crash recovery — a crash is detected (`running`/`pausing` with a stale own
  lock), torn state and journal tails are repaired under the lock, discarded
  and revalidated units are journaled, and execution continues from the last
  checkpoint; proven with kill and truncation injection.
- Deterministic publication projection: a versioned render/selection layer
  mapping verified workspace artifacts to the plan §4 publication file names
  by run mode, selecting and recording a timing view per export (ADR 0026),
  fabricating nothing (`unavailable` manifest entries instead), and
  participating in invalidation with its own Stage version.
- Staging, atomic publish, manifest, and latest pointer: candidates assemble
  with recorded hashes in staging; publication is one whole-directory rename
  after an `st_dev` same-filesystem check, followed by re-hashing every
  published file against the RunBundle manifest with bidirectional
  manifest ↔ disk coverage; `latest.json` per source advances only for
  publishable runs and never copies artifacts (ADR 0051).
- Reports, inventory, and cleanup plan: `quality-report.md/json` aggregates
  recorded per-stage gate outcomes without re-running them,
  `processing-report.md` carries the plan §18.1 sections, and
  `run-inventory.json` records every touched path with the plan §18.2
  eleven-field record including deletion class and consequence; every
  ordinary failure path publishes the Minimal RunBundle; no cleanup command
  exists and nothing is deleted automatically.
- The orchestration CLI: `vcp run/status/pause/resume/cancel/verify/
  inventory` behind the Explicit orchestration command boundary — run is
  non-interactive end-to-end, status diagnoses stale-running without
  mutating, pause/cancel only write control requests, verify operates at the
  hash layer only, and the sixteen per-phase expert commands are untouched.
- Improvement runs: `vcp improve --from-run --asr` creates a new plan and
  run carrying forward artifacts only from the named published bundle with
  recorded source run id and hashes, publishing through the standard
  staging/atomic-publish path under the standard latest eligibility.
- The Phase 9 CLI contract is proved offline end to end; the
  machine-checkable exit gates and file inventory are recorded in
  [PHASE_09_INVENTORY.json](PHASE_09_INVENTORY.json) (21 confirmed summary
  gates mapping the phase plan's five 退出门禁 plus the specification's
  eight derived gates, machine-checked by
  `tests/acceptance/test_phase_09_inventory.py`).

## Recorded Deviations

- `vcp models plan/download/verify` is deferred, per the approved
  specification, to the separately authorized model-prototype session; the
  Phase 10 CLI acceptance list deliberately omits it.
- The per-phase functions `vcp run` composes cannot execute offline (they
  require a model, real media, and the network), so the run loop and stage
  composition are proven through controlled executor seams; real end-to-end
  composition is exercised in real-world testing.
- Resuming a Run decision pause is validate-and-handoff: plan §12 makes
  `incomplete` terminal, so `vcp resume --decision` validates and journals
  the accepted decision against the recorded requirement without re-entering
  execution in place; continuation follows the retained per-phase decision
  contracts.

## Final Verification

The final commands ran from the project root through the project `.venv`.

| Gate | Result |
| --- | --- |
| `pytest -q` | 1034 passed in 3.85s |
| `ruff check .` | All checks passed |
| `ruff format --check .` | 134 files already formatted |
| `mypy src` | Success: no issues found in 57 source files |
| Phase inventory summary | 21 exit-gate booleans, all `true` |

Closure note: per-ticket intermediate gate outputs were not retained by the
implementing sessions; verification is anchored to the current-head run
above. Ticket status bookkeeping (all twelve files, including acceptance
checkboxes) and the `project-state.json` `completed` transition were
performed at closure on the maintainer's explicit instruction, after the
inventory had recorded all exit gates as confirmed.

Verification used only project-owned synthetic fixtures and the retained
controlled offline adapters inside synthetic test project roots. It did not
download, install, or invoke a model, access user media or a network, invoke
a paid API, write the repository's own `outputs/`, or mark the project
`production_validated`.
