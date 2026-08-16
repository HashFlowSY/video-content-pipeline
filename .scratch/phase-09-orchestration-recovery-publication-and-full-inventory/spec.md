# Phase 9: Orchestration, Recovery, Publication, And Full Inventory

## Domain routing

Begin with the [Context Map](../../CONTEXT-MAP.md), then read
[Orchestration](../../docs/contexts/orchestration/CONTEXT.md) — the owner of
this phase — and its dependencies:
[Media Foundation](../../docs/contexts/media-foundation/CONTEXT.md),
[Source Planning](../../docs/contexts/source-planning/CONTEXT.md),
[Subtitles](../../docs/contexts/subtitles/CONTEXT.md),
[Audio Analysis](../../docs/contexts/audio-analysis/CONTEXT.md),
[Text Analysis](../../docs/contexts/text-analysis/CONTEXT.md),
[Transcription](../../docs/contexts/transcription/CONTEXT.md), and
[Visual-Text](../../docs/contexts/visual-text/CONTEXT.md). The authoritative
contract is [docs/PHASE_09_SPECIFICATION.md](../../docs/PHASE_09_SPECIFICATION.md).

Type: enhancement
Status: ready-for-agent
Labels: ready-for-agent
Phase: 9
Published: 2026-08-16

## Problem Statement

After Phase 8 the pipeline is a set of proven prototypes behind sixteen
expert commands: each stage revalidates its inputs and retains immutable
workspaces, but nothing composes them. There is no run: no run identity, no
persisted state machine, no lock that actually serializes heavy work, no
pause/cancel a second terminal can issue, no crash recovery, and — most
importantly — no publication. `outputs/` has never been written; every ADR
since Phase 5 defers to "a future authorized publication stage". A user with
a confirmed plan must drive six commands by hand, carry report ids between
them, and gets no bundle, no manifest, no inventory, and no answer to "what
did this run touch and what may I delete?"

## Solution

One orchestration layer, specified by grilling consensus (2026-08-16). A new
`orchestration` Context owns runs and publication (ADR 0050). `vcp run`
executes a confirmed plan non-interactively by invoking the existing
per-phase functions in-process over a stage DAG of `(stage, Part)` atomic
units, each guarded by a Stage invalidation key (input hashes + stage-scoped
config subset + manual Stage version, ADR 0052). The run process is the sole
writer of `run-state.json` and `events.jsonl`; `pause`/`cancel` write control
request files observed at unit boundaries; a heavy-task lock serializes heavy
runs; a crash is a detected stale-running condition recovered from the last
checkpoint (ADR 0053). Verified workspace artifacts are deterministically
projected into publication formats with a recorded timing view (ADR 0026),
assembled in a staging area, and published by one whole-directory atomic
rename with post-publish hash reverification (ADR 0051). Every ordinary
failure still publishes a Minimal RunBundle; `latest.json` per source points
at the recommended publishable run; the run inventory and cleanup plan cover
every touched path; nothing ever overwrites a published run. `vcp improve`
creates a new run that carries forward artifacts from a named published
bundle by recorded hash. All contracts are proven offline inside synthetic
test project roots — the repository's own `outputs/` remains untouched.

## User Stories

1. As a pipeline user, I want `vcp run --plan` to execute my confirmed plan
   end-to-end without prompting, so that a 4-hour job runs unattended.
2. As a pipeline user, I want to pause from a second terminal and resume
   later, so that I can reclaim my machine without losing completed work.
3. As a pipeline user, I want a power loss or forced kill to cost me at most
   the current stage unit, so that long runs are economically recoverable.
4. As a pipeline user, I want every run — even a failed one — to publish a
   bundle with manifest, reports, and inventory, so that I always have an
   auditable record of what happened and what exists.
5. As a pipeline user, I want `vcp verify` to prove by hash that a published
   bundle is intact, so that I can trust old outputs.
6. As a pipeline user, I want `latest.json` to point at the best publishable
   run per source, so that consumers need no run-id knowledge.
7. As a pipeline user, I want `vcp improve` to upgrade intervals of a
   published run into a new run without touching the old bundle, so that
   improvement is safe and comparable.
8. As a pipeline user, I want the inventory to tell me what every file was
   for and what deleting it would cost, so that cleanup is my informed
   decision — never automatic.

## Implementation Decisions

- In-process composition: `vcp run` calls the existing stage functions; the
  sixteen per-phase commands remain unchanged as the expert surface.
- Scope: all ten plan §16 work items in this phase; `vcp models *` deferred
  to the authorized model-prototype session; `vcp improve` included.
- State machine exactly as plan §12; `queued` is a transient lock wait — no
  persistent queue; a second heavy run fails fast.
- Run decision pauses (resource envelope, model acquisition, resource
  confirmation) map to run status `incomplete` + machine-readable
  `required_decision`; `paused` is reserved for user pauses; `vcp resume`
  handles paused, decision, and crash cases under one contract.
- Atomic unit is `(stage, Part)`; collection-level stages are single units;
  checkpoints only at unit boundaries; no sub-Part checkpointing.
- Run-scoped adoption only (ADR 0052); manual workspaces are never scavenged;
  improve's published-bundle carry-forward is the sanctioned exception.
- Publication projection is deterministic, versioned, does no new analysis,
  and records timing-view selection per ADR 0026.
- Minimal RunBundle floor: manifest, processing report, run inventory, both
  quality reports, diagnostics with events snapshot.
- Latest pointer is per-source; eligibility is `complete`,
  `complete_with_warnings`, or published partial results; failed runs never
  advance it.
- `vcp verify` is hash-layer only; quality gates are not re-run.
- Cleanup plan lives in inventory deletion classes and the processing
  report's cleanup section; no cleanup command; no automatic deletion.
- Identity and layout follow plan §4: collection `source-id`, `run-id` from
  run time + plan id + config hash, staging under
  `work/<source-id>/<run-id>/staging/`, bundles under
  `outputs/<source-id>/<run-id>/`.

## Testing Decisions

- Offline only, inside synthetic test project roots built from hash-pinned
  fixtures and the retained controlled offline adapters; the repository's own
  `outputs/` stays empty.
- Crash recovery is proven with kill and state/journal truncation injection;
  systematic fault-injection matrices remain Phase 10.
- Prior-phase contract assertions "`outputs/` does not exist" are
  reformulated as "non-publication commands never write `outputs/`".
- `guarantees_asserted_at_cli` keeps model/network/frame guarantees at
  `not_attempted`; `outputs_publication` becomes `synthetic_roots_only`.
- Exit gates: the plan's five 退出门禁 plus derived gates (single-writer
  state, atomic same-filesystem publish, latest eligibility, non-interactive
  execution, run-scoped adoption, decision-pause mapping, crash recovery,
  hash-layer verify), machine-checked by
  `tests/acceptance/test_phase_09_inventory.py` against
  `docs/PHASE_09_INVENTORY.json`.

## Out of Scope

- `vcp models plan/download/verify`; model acquisition or execution; real
  media; network access.
- Persistent cross-process queue or daemon; sub-Part checkpoints; cross-run
  workspace adoption (future explicit flag only); cleanup command.
- Real-world quality claims, `human_verified` workflows, real-world testing,
  or `production_validated`.

## Further Notes

- The heavy-task lock realizes ADR 0032's serialization rule at run level;
  per-context serialized-execution terms keep their meanings.
- Stage-version discipline: any behavior change increments the stage's
  version, otherwise resume silently reuses old-behavior outputs. This is a
  review checklist item for every stage-touching change from now on.
- Tickets live in `issues/`, numbered from 01; the final ticket verifies exit
  gates and publishes the completion record on maintainer instruction.
