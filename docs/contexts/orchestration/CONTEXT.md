# Orchestration Context

This Context owns the vocabulary for recoverable runs and authorized
publication: run identity and state, stage units and their invalidation,
process control, crash recovery, candidate staging, atomic publication, and
the published RunBundle with its manifest, reports, inventory, and latest
pointer. It composes every upstream evidence Context and is the only Context
that writes `outputs/`.
Operational mechanics and exact thresholds remain in the linked specifications
and ADRs.

Relevant global decisions include
[ADR 0050](../../adr/0050-introduce-an-orchestration-context-that-owns-runs-and-publication.md),
[ADR 0051](../../adr/0051-publish-runbundles-by-whole-directory-atomic-rename.md),
[ADR 0052](../../adr/0052-adopt-stage-outputs-run-scoped-with-stage-versioned-invalidation-keys.md),
[ADR 0053](../../adr/0053-use-single-writer-run-state-with-file-based-control-requests.md),
[ADR 0026](../../adr/0026-keep-adopted-alignment-timing-derived.md),
[ADR 0032](../../adr/0032-serialize-phase-5-heavy-analysis.md),
[ADR 0034](../../adr/0034-keep-phase-5-analysis-in-immutable-workspaces.md),
[ADR 0041](../../adr/0041-keep-phase-6-text-analysis-in-immutable-workspaces.md), and
[ADR 0046](../../adr/0046-recompute-affected-parts-with-carried-forward-analysis.md).

## Language

**Run**:
One recoverable, auditable execution of a confirmed RunPlan, identified by a
Run identity and governed by the run state machine
(`planned/queued/running/pausing/paused/complete/complete_with_warnings/incomplete/failed/cancelled`).
_Avoid_: ad hoc pipeline invocation

**Run identity**:
The immutable run id formed from the run start time, the immutable plan id,
and the configuration hash; any model, prompt, language, Part, network, or
quality configuration change creates a new plan or run, never a silent
mutation of an existing one.
_Avoid_: reused run directory

**Run state document**:
The atomically replaced `run-state.json` that records the run's status, stage
units, adopted outputs, invalidation keys, and any required decision.
_Avoid_: shared mutable state file

**Run events journal**:
The append-only `events.jsonl` audit record of state transitions, control
requests, decision pauses, publication, and recovery.
_Avoid_: rotating log

**Single-writer run state**:
The rule that the run process is the only writer of the Run state document and
Run events journal; control commands communicate exclusively through Control
requests.
_Avoid_: cross-process state write

**Stage unit**:
The atomic unit of work, checkpointing, pause, and recovery — one stage
applied to one Part, or one collection-level stage; checkpoints exist only at
completed stage-unit boundaries.
_Avoid_: sub-Part checkpoint

**Stage invalidation key**:
The key deciding whether a completed stage unit may be adopted: its input
hashes, the hash of the stage-scoped configuration subset, and the Stage
version. A configuration change invalidates only the affected stages and
their downstream units.
_Avoid_: whole-config hash

**Stage version**:
A manually incremented per-stage implementation version participating in the
Stage invalidation key; any behavior change must increment it, or prior
outputs would be silently reused.
_Avoid_: silent behavior reuse

**Run-scoped adoption**:
A run adopts only stage-unit outputs recorded in its own Run state document
and validated by their Stage invalidation keys; retained workspaces from
manual per-phase commands are never scavenged.
_Avoid_: workspace scavenging

**Heavy-task lock**:
The lock that serializes heavy runs, recording the holder's run id, process
id, and process start time; a dead holder makes the lock stale. `queued` is
the transient state of a run created while the lock is unavailable — there is
no persistent cross-process queue.
_Avoid_: advisory queue flag

**Control request**:
A request recorded by `vcp pause` or `vcp cancel` for the run process to
observe at the next stage-unit boundary; the request and its handling are
themselves audited events.
_Avoid_: process signal

**Run decision pause**:
A stage's required user decision surfacing at the run level as status
`incomplete` with a machine-readable required decision, resumed only by an
explicit matching decision; strictly distinct from a user-initiated pause.
_Avoid_: merged pause state

**Crash recovery**:
The resume path for a run whose state says `running` while its Heavy-task
lock is stale: discard work past the last checkpoint, revalidate checkpointed
stage units by their invalidation keys, record a recovery event, and
continue. A crash is a detected condition, never a persisted state.
_Avoid_: persisted crashed state

**Non-interactive run execution**:
`vcp run` never prompts: every run-affecting choice is front-loaded at plan
confirmation, and any missing required choice becomes a Run decision pause.
_Avoid_: mid-run prompt

**Publication projection**:
The deterministic, versioned projection of verified workspace artifacts into
publication file names and formats, selecting a timing view (ADR 0026) with
recorded basis and performing no new analysis and no content change.
_Avoid_: regeneration at publish time

**Staging area**:
The candidate RunBundle assembled in the run's staging directory in final
bundle layout, on the same filesystem as `outputs/`.
_Avoid_: partial in-place output

**Atomic publish**:
Publication as one whole-directory rename of the Staging area into
`outputs/`, preceded by a same-filesystem check and followed by hash
reverification; a failed publish leaves nothing visible under `outputs/`.
_Avoid_: file-by-file publication

**RunBundle**:
The immutable published bundle at `outputs/<source-id>/<run-id>/`; it is
never overwritten, and every formal file is hash-verifiable through the
RunBundle manifest.
_Avoid_: analysis workspace

**Publication boundary**:
The separately authorized boundary that promotes verified artifacts into a
RunBundle; upstream Contexts retain evidence without publishing it.
_Avoid_: implicit output write

**RunBundle manifest**:
The bundle's self-describing root (`manifest.json`) listing every expected
artifact with status `valid`, `partial`, `invalid`, or `unavailable` and its
hash; the manifest and the on-disk bundle must match exactly, and no
placeholder file is fabricated for an unavailable artifact.
_Avoid_: implicit directory listing

**Minimal RunBundle**:
The publication floor guaranteed for every ordinary failure: the manifest,
the processing report, the run inventory, both quality reports, and
diagnostics including an events snapshot.
_Avoid_: empty failure directory

**Latest pointer**:
The per-source `latest.json` naming the recommended publishable run —
`complete`, `complete_with_warnings`, or a run with published partial results;
a purely failed run never advances it, and it copies no artifacts.
_Avoid_: global latest

**Run inventory**:
The `run-inventory.json` record of every used, created, modified, downloaded,
or published path with action, purpose, hashes, and a deletion class with its
consequence.
_Avoid_: partial file list

**Cleanup plan**:
The explicit declaration of deletable items through Run inventory deletion
classes and the processing report's cleanup section; there is no cleanup
command and no automatic deletion — deletion is always a user action.
_Avoid_: automatic cleanup

**Improvement run**:
A new plan and run created by `vcp improve` from a named published RunBundle,
carrying forward published artifacts by recorded source run id and hash
(ADR 0046 pattern); the prior bundle is never modified.
_Avoid_: in-place run upgrade

**Explicit orchestration command boundary**:
The orchestration surface `vcp run/status/pause/resume/cancel/verify/
inventory/improve`; the per-phase commands remain the retained expert surface
and orchestration invokes their functions in-process.
_Avoid_: hidden orchestration entry point

**Golden run**:
The fault-free reference execution whose observed durable writes enumerate
Fault points and fix the expected outcome of a scenario.
_Avoid_: happy path sample

**Fault point**:
One durable write call enumerated by a Golden run; the unit over which the
Fault matrix iterates.
_Avoid_: random crash site

**Fault class**:
One kind of injected failure at a Fault point: process death, exhausted
disk, or a torn write.
_Avoid_: generic error

**Fault matrix**:
The exhaustive replay of a scenario across every Fault point and Fault
class, asserting the same recovery invariants in every cell.
_Avoid_: chaos testing
