# Phase 9 Specification: Orchestration, Recovery, Publication, And Full Inventory

## Domain routing

Begin with the [Context Map](../CONTEXT-MAP.md), then read
[Orchestration](contexts/orchestration/CONTEXT.md) — the owner of this phase —
and its dependencies: [Media Foundation](contexts/media-foundation/CONTEXT.md),
[Source Planning](contexts/source-planning/CONTEXT.md),
[Subtitles](contexts/subtitles/CONTEXT.md),
[Audio Analysis](contexts/audio-analysis/CONTEXT.md),
[Text Analysis](contexts/text-analysis/CONTEXT.md),
[Transcription](contexts/transcription/CONTEXT.md), and
[Visual-Text](contexts/visual-text/CONTEXT.md). Orchestration composes every
evidence Context in-process and is the only Context that writes `outputs/`
(ADR 0050). Transcription and visual-text stages execute conditionally by run
mode; their vocabulary remains a full dependency of this contract.

## Status

`approved_for_implementation_planning` (grilling consensus approved
2026-08-16). Verification will be offline only: no model is downloaded,
installed, or invoked, no user media or network is accessed, and publication
is exercised only inside synthetic test project roots — the repository's own
`outputs/` remains untouched. The phase claims no domain quality,
`model_audited`, `human_verified`, real-world testing, or production
validation.

## Objective

Compose the Phase 2–8 prototypes into a single recoverable, auditable CLI:
a stage DAG over `(stage, Part)` atomic units with hash-and-version
invalidation keys, a single heavy-task lock, pause/resume/cancel and crash
recovery under a single-writer run state, candidate staging with
whole-directory atomic publication, and the first governed `outputs/` writes —
the RunBundle with manifest, quality and processing reports, full run
inventory, cleanup plan, and per-source latest pointer. Every ordinary
failure still publishes a Minimal RunBundle; nothing ever overwrites a
published run.

## Public Contract

```text
vcp run --plan <plan-id> [--json]
vcp status [--run <run-id>] [--json]
vcp pause --run <run-id> [--json]
vcp resume --run <run-id> [--decision <decision>] [--json]
vcp cancel --run <run-id> [--json]
vcp verify --run <run-id> [--json]
vcp inventory --run <run-id> [--json]
vcp improve --from-run <run-id> --asr <part|range|all> [--json]
```

- These commands are the Explicit orchestration command boundary. The sixteen
  retained per-phase commands remain the expert and debugging surface;
  `vcp run` invokes their underlying functions in-process and never spawns
  them as subprocesses.
- `vcp run` is Non-interactive run execution: every run-affecting choice is a
  Front-loaded plan choice fixed at plan confirmation; a missing required
  choice becomes a Run decision pause, never a prompt.
- `vcp status` without `--run` lists known runs; with `--run` it reports the
  persisted state, including a stale-running diagnosis when the state says
  `running` but the Heavy-task lock is stale.
- `vcp resume` serves three cases with one contract: continuing a `paused`
  run (no decision required), answering a Run decision pause (`--decision`
  must match the recorded required decision), and Crash recovery of a
  stale-running run.
- `vcp verify` operates at the hash layer only: it re-hashes every published
  file against the RunBundle manifest, checks manifest-versus-disk coverage
  in both directions, and validates the Run inventory structure. It does not
  re-run quality gates.
- `vcp models plan/download/verify` is deferred to the separately authorized
  model-prototype session (registry currently holds zero entries; the Phase
  10 CLI acceptance list does not include it).
- All commands emit a single machine-readable JSON object on stdout,
  following the existing CLI error contract.

## Run Identity And Layout Contract

- A collection-level `source-id` derives from ordered Part content hashes and
  collection structure; a single medium uses its content hash (plan §4 rules).
- Run identity: `run-id` is formed from the run start time, the immutable
  plan id, and the configuration hash. Any model, prompt, language, Part,
  network, or quality configuration change creates a new plan or run.
- Run-owned state lives under `work/<source-id>/<run-id>/` — the Run state
  document, the Run events journal, stage workspaces, `tmp/`, and the Staging
  area `work/<source-id>/<run-id>/staging/` assembled in final RunBundle
  layout.
- Published bundles live at `outputs/<source-id>/<run-id>/`; the per-source
  Latest pointer lives at `outputs/<source-id>/latest.json`. An existing run
  directory is never overwritten (ADR 0051).

## Run State And Control Contract

- The state machine is exactly the plan §12 machine:
  `planned -> queued -> running -> complete | complete_with_warnings |
  incomplete | failed | cancelled`, with `running -> pausing -> paused ->
  running`. No states are added. `queued` is the transient Heavy-task lock
  wait; there is no persistent cross-process queue, and a second heavy run
  that cannot obtain the lock fails fast with a clear reason.
- Single-writer run state (ADR 0053): the run process is the only writer of
  `run-state.json` (atomic replace) and `events.jsonl` (append-only).
  `vcp pause` and `vcp cancel` write Control request files observed at the
  next Stage unit boundary; request, observation, and transition are all
  journaled events.
- `paused` means the run process exited after a clean boundary; `vcp resume`
  starts a new process. `cancel` stops later stages and still publishes
  results that already exist.
- A stage-required user decision (resource-envelope pause, model acquisition,
  resource confirmation — the retained per-phase pause vocabulary) surfaces
  as a Run decision pause: run status `incomplete` with a machine-readable
  required decision. It is strictly distinct from a user pause.
- Crash recovery: a crash is a detected condition (`running` state plus stale
  Heavy-task lock), never a persisted state. Resume discards work past the
  last checkpoint, revalidates checkpointed units by their Stage invalidation
  keys, journals a recovery event, and continues.
- ASR, aligner, diarization, OCR, and text models never load concurrently;
  the Heavy-task lock realizes the plan's single-heavy-run rule and the
  serialized execution contracts (ADR 0032 and the per-context serialized
  execution terms).

## Stage DAG And Adoption Contract

- The DAG nodes are the existing prototypes composed in-process: source and
  plan revalidation → subtitles → audio-analysis → {transcription |
  enhancement} by mode → text-analysis (or affected-Part re-analysis) →
  visual-text (only when enabled) → Publication projection → staging →
  publish. Dependencies follow the Context Map routes.
- The atomic unit of work, checkpointing, pause, and recovery is the Stage
  unit: one stage applied to one Part, or one collection-level stage.
  Checkpoints exist only at completed stage-unit boundaries; sub-Part
  checkpointing is out of scope.
- Each stage declares a Stage invalidation key: input hashes, the hash of its
  stage-scoped configuration subset, and a manually incremented Stage version
  (ADR 0052). A configuration change invalidates only affected stages and
  their downstream units. Development discipline: any behavior change must
  increment the stage's version — silent reuse of prior-behavior outputs is
  the failure this rule exists to prevent.
- Run-scoped adoption: on resume, a completed Stage unit is re-used only if
  recorded in this run's own Run state document and its invalidation key
  still matches. Retained workspaces from manual per-phase commands are never
  scavenged. Every adoption applies the revalidation-before-use pattern
  (ADR 0024/0025/0033 lineage).
- Partial results publish per Part: failed Parts do not block publishing
  completed Parts and the collection-level partial artifacts (plan §7).

## Publication Contract

- Publication projection: verified workspace artifacts are deterministically
  projected into the plan §4 publication file names and formats
  (`subtitles.*`, `transcript.<basis>.*`, `content-report.md`,
  `segments.json`, `correction-log.json`). The projection performs no new
  analysis and no content change; it selects and records a timing view
  (ADR 0026: RawPtsTime evidence, PartRelativeTime for per-Part exports,
  CollectionVirtualTime for collection exports) and carries its own Stage
  version. Unavailable artifacts are recorded as `unavailable` in the
  manifest; no placeholder files are fabricated.
- Staging and Atomic publish (ADR 0051): candidates assemble in the Staging
  area with recorded hashes; publication is one whole-directory rename after
  an `st_dev` same-filesystem check (mismatch errors, never silent copying),
  followed by re-hashing every published file against the RunBundle manifest.
- Minimal RunBundle: every ordinary failure — including a run that fails
  before any stage completes — still publishes `manifest.json`,
  `processing-report.md`, `run-inventory.json`, `quality-report.md`,
  `quality-report.json`, and `diagnostics/` (including an events snapshot).
- The RunBundle manifest lists every expected artifact with status
  `valid | partial | invalid | unavailable` and hash; manifest and disk must
  match exactly in both directions.
- Latest pointer: per-source `latest.json` names the recommended publishable
  run — `complete`, `complete_with_warnings`, or a run with published partial
  results. A purely failed run never advances it. It stores a pointer only,
  never copies artifacts.
- Cleanup plan: deletion classes and consequences live in the Run inventory
  and the processing report's cleanup section. There is no cleanup command
  and no automatic deletion; source media, raw ASR, alignment evidence, and
  formal outputs default to retained.

## Improvement Contract

- `vcp improve --from-run <run-id> --asr <part|range|all>` creates an
  Improvement run: a new plan and a new run id. The prior published RunBundle
  is never modified.
- Carried-forward artifacts are read only from the named published RunBundle
  — never from workspaces — and are recorded in the new run's manifest and
  reports with source run id and artifact hashes (the ADR 0046 carry-forward
  pattern at run level; the sanctioned exception to Run-scoped adoption in
  ADR 0052).
- The improvement path routes through the retained enhancement and
  affected-Part re-analysis contracts unchanged, then re-projects and
  publishes as a normal run.

## Status, Resource, And Recovery Contract

- Run statuses follow plan §2.5: `complete`, `complete_with_warnings`,
  `incomplete`, `failed`, `cancelled`; artifact statuses are
  `valid | partial | invalid | unavailable`. Automated `complete` states
  `model_audited` at most (plan §17.6) and never claims human verification.
- `cancel` publishes existing results as a bundle whose manifest records what
  is missing; an ordinary exception publishes a `failed` Minimal RunBundle.
- Resource estimation reuses the Phase 3 phase-bounded estimate and disk
  headroom contracts before heavy stages; envelope violations surface as Run
  decision pauses, never silent parameter changes.
- No automatic retry anywhere: a retry is an explicit user resume or a new
  run and never overwrites prior evidence.

## Reporting And Language

- `processing-report.md` carries the plan §18.1 required sections, including
  the fixed project-stage line, environment and lockfile identity, tools and
  models with hashes, network and external reads, created/modified/published
  paths, measured time, peak memory and disk deltas, warnings, review-needed
  intervals, and the cleanup section.
- `run-inventory.json` records every used, created, modified, downloaded, or
  published path with the plan §18.2 eleven-field record, including
  `deletion_class` and `deletion_consequence`, covering models, caches,
  workspaces, staging, and published files.
- `quality-report.md/json` aggregates the per-stage gate outcomes from
  retained stage reports (plan §17 gate families) without re-running them,
  and records the projection's timing-view selections and bases.
- All orchestration commands emit machine-readable JSON; readable report
  prose defaults to Chinese (Phase 6 report language boundary); artifact text
  keeps its source language.

## Offline Test Contract

Tests use only hash-pinned synthetic fixtures and the retained controlled
offline adapters inside synthetic test project roots, asserting deterministic
contract properties: non-interactive run execution over front-loaded plan
choices; the exact §12 state machine with `queued` as transient lock wait;
single-writer state and journaled control requests; pause at stage-unit
boundaries; cancel-still-publishes; Run decision pauses distinct from user
pauses with matching-decision resumes; kill and truncation injection proving
crash recovery (discard past checkpoint, revalidate by invalidation key,
journal recovery, continue); stage-version and config-subset invalidation
with downstream-only effect; run-scoped adoption refusing manual workspaces;
projection determinism and timing-view recording; staging layout, `st_dev`
precheck, whole-directory rename atomicity, and post-publish hash
reverification; Minimal RunBundle on every ordinary failure path; manifest ↔
disk bidirectional coverage; latest-pointer eligibility (failed runs never
advance it); no-overwrite of existing run directories; verify's hash-layer
scope; inventory coverage of all used/created/modified/deletable files; and
improvement runs carrying forward only from published bundles with recorded
provenance.

The exit gates map the phase plan's 退出门禁 list (minimal RunBundle on every
ordinary failure; completed valid stages do not re-run on resume; any formal
file is hash-verifiable; outputs never overwrite an old run; inventory covers
all used, created, modified, and deletable files) plus the derived gates from
this specification: single-writer state, atomic same-filesystem publication,
latest-pointer eligibility, non-interactive execution, run-scoped adoption,
decision-pause mapping, crash-recovery semantics, and hash-layer verify.

`guarantees_asserted_at_cli` keeps `model_execution`, `model_acquisition`,
`network_access`, and `frame_extraction` at `not_attempted`;
`outputs_publication` changes for the first time to
`synthetic_roots_only` — publication is exercised exclusively inside
synthetic test project roots, and a repository-level assertion proves the
repository's own `outputs/` still contains no published bundle. Prior-phase
contract assertions of the form "`outputs/` does not exist" are reformulated
to "non-publication commands never write `outputs/`".

## Out Of Scope

- `vcp models plan/download/verify` — deferred to the separately authorized
  model-prototype session.
- Model acquisition, installation, real model execution, real media, network
  access, or real-world quality claims.
- A persistent cross-process run queue or scheduling daemon.
- Sub-Part checkpointing (ASR chunk-level resume inside one Part).
- Cross-run adoption of manual workspaces (possible later only as an explicit
  opt-in flag; see ADR 0052).
- A cleanup command or any automatic deletion.
- Human verification workflows (`human_verified` recording beyond the plan
  §17.6 vocabulary), real-world testing, or a `production_validated` state.

## Related Decisions

This specification uses the orchestration vocabulary in the
[Context Map](../CONTEXT-MAP.md) and
[Orchestration Context](contexts/orchestration/CONTEXT.md) and is governed in
particular by
[ADR 0050](adr/0050-introduce-an-orchestration-context-that-owns-runs-and-publication.md),
[ADR 0051](adr/0051-publish-runbundles-by-whole-directory-atomic-rename.md),
[ADR 0052](adr/0052-adopt-stage-outputs-run-scoped-with-stage-versioned-invalidation-keys.md), and
[ADR 0053](adr/0053-use-single-writer-run-state-with-file-based-control-requests.md),
with the promotion boundary anticipated by
[ADR 0034](adr/0034-keep-phase-5-analysis-in-immutable-workspaces.md) and
[ADR 0041](adr/0041-keep-phase-6-text-analysis-in-immutable-workspaces.md),
timing-view selection from
[ADR 0026](adr/0026-keep-adopted-alignment-timing-derived.md),
heavy-task serialization from
[ADR 0032](adr/0032-serialize-phase-5-heavy-analysis.md),
carry-forward provenance from
[ADR 0046](adr/0046-recompute-affected-parts-with-carried-forward-analysis.md),
and the revalidation-before-use lineage of
[ADR 0024](adr/0024-revalidate-evidence-before-plan-confirmation.md),
[ADR 0025](adr/0025-revalidate-before-subtitle-processing.md), and
[ADR 0033](adr/0033-revalidate-all-phase-5-analysis-inputs.md).
