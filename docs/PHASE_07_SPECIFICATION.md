# Phase 7 Specification: Full ASR Transcription and Local Enhancement

## Domain routing

Begin with the [Context Map](../CONTEXT-MAP.md), then read [Media
Foundation](contexts/media-foundation/CONTEXT.md), [Source
Planning](contexts/source-planning/CONTEXT.md),
[Subtitles](contexts/subtitles/CONTEXT.md), [Audio
Analysis](contexts/audio-analysis/CONTEXT.md), and
[Transcription](contexts/transcription/CONTEXT.md). [Text
Analysis](contexts/text-analysis/CONTEXT.md) participates through
affected-Part re-analysis. Audio analysis is a required dependency of
transcription (ADR 0043): the coverage-based quality gates and
suspicious-interval detection need voice-activity evidence.

## Status

`approved_for_implementation_planning`. The user approved this specification
and its atomic ticket breakdown on 2026-08-15; `project-state.json` records
Phase 7 `implementation_planning`. The phase will be verified offline only: no model is downloaded, installed, or
invoked, no user media or network is accessed, `outputs/` is not written, and
the phase claims no domain quality, `model_audited`, `human_verified`,
real-world testing, or production validation.

## Objective

From a revalidated confirmed RunPlan, retained subtitle evidence, and a
required retained Audio analysis report, create immutable transcription
evidence: a full verbatim transcript for subtitle-unavailable or explicitly
upgraded sources, and interval-scoped enhanced subtitles for user-identified
problems — each gated, arbitrated deterministically, and feeding a targeted
re-analysis of affected Parts. ASR output is a candidate and never evidence
authority.

## Public Contract

```text
vcp transcribe <plan-id> <subtitle-report-id> <audio-report-id> [--json]
vcp resume-transcription <report-id> --decision <decision> [--json]
vcp enhance <plan-id> <subtitle-report-id> [--audio-report <report-id>]
  (--part <part-id> | --range <part-id>:<start>-<end>
   | --cue <part-id>:<cue-id>)... [--json]
vcp resume-enhancement <report-id> --decision <decision> [--json]
```

- `vcp transcribe` is the sole full-ASR start command. Its precondition is
  either a retained `subtitle_unavailable_requires_asr_plan` handoff or an
  explicit user demand to upgrade all selected Parts; a subtitle-priority run
  never triggers it automatically. It creates a new immutable transcription
  workspace and `transcription-report.json`.
- `vcp enhance` is the sole local-enhancement start command. Its target scope
  comes only from the user (`--part`, `--range`, `--cue`); automatic
  suspicious-interval discovery belongs to the full-ASR path only. It creates
  a new immutable enhancement workspace.
- Both `resume-*` commands must name a retained report and an explicit user
  decision. They never auto-resume or silently change identity-bound inputs.
- The two commands are deliberately separate: only `transcribe` may produce
  `verbatim` artifacts; `enhance` never claims full verbatim completeness.
- The authoritative reports are JSON. Readable artifacts are deterministically
  rendered, remain in the workspace, and are not published until the future,
  separately authorized publication stage.

## Input And Revalidation Contract

Before an attempt may execute, it must exactly revalidate:

- confirmed RunPlan and SourceArtifact hashes;
- retained Subtitle candidate report (including a
  `subtitle_unavailable_requires_asr_plan` handoff where applicable);
- retained Audio analysis report hash and its bound input identities —
  required for `transcribe`, optional for `enhance`;
- Controlled offline ASR adapter identity, or eligible real-model identity;
- versioned suspicion detection rules, arbitration rules, and gate versions;
  and
- for enhancement, every user-named Part, range, and cue against retained cue
  identities and actual stream coverage.

Any drift blocks the attempt and requires a new attempt. On a
subtitle-unavailable source, `transcribe` must pause at the Full-ASR resource
confirmation pause and record the user's explicit resource-plan confirmation
before execution.

## Capability And Eligibility Contract

- Capabilities are provider-neutral: `asr_primary` and `asr_review`
  (Transcription capability contract, per ADR 0036 precedent). Candidates come
  from `models/registry.json` eligibility evaluation only; no download, no
  execution, no network.
- The review capability is subject to the Independent-model review
  requirement: a same-model retry is a recovery attempt and is never reported
  as independent review.
- Qwen3-ASR-1.7B (`asr_primary`) and WhisperKit / Whisper large-v3
  (`asr_review`) are registered as research candidates with license, source,
  revision, and eligibility fields. Real local-run, language, and memory
  validation is a recorded deviation from the phase plan's original work items
  1–2: it is deferred to an explicitly authorized model-prototype session
  before real-world testing.
- No eligible capability yields an immutable
  `model_acquisition_required` report with no transcription evidence.

## Transcription Evidence Contract

- ASR output enters only through a versioned output projection; an incomplete
  or schema-invalid projection is `model_output_invalid` and invalidates the
  attempt. Raw output is restricted local audit evidence.
- Projected cues live on the canonical timeline: exact rational times, within
  actual stream coverage, monotonic order, half-open intervals, no processing
  duplication. Cues violating these gates are rejected with reasons, never
  silently repaired.
- Suspicious intervals are found only by Versioned suspicion detection rules:
  VAD coverage, confidence, repetition, language switching, numbers/entities,
  and coverage checks — deterministic detectors with conservative defaults and
  `calibration_required` marks. Real thresholds are calibrated only in
  real-world testing.
- The second ASR runs only on suspicious intervals by default; a full-length
  review run requires an explicit user decision.
- Disagreements are resolved by Deterministic transcription arbitration
  (ADR 0044); undecided cases become Unresolved transcription conflicts:
  primary text stands, both candidates retained, interval `review-needed`.
  The second model never automatically decides truth.

## Artifact Semantics Contract

- `verbatim` artifacts (`subtitles.verbatim.*`, `transcript.verbatim.*`)
  come only from a complete full-ASR run that passed coverage checks; only
  they may upgrade `audio_completeness` (Audio-completeness upgrade).
- `enhanced` artifacts (`subtitles.enhanced.*`, `transcript.enhanced.*`) are
  interval-scoped and carry Cue-level transcription provenance
  (`subtitle_track` or `asr`) on every cue. They never claim full verbatim
  completeness and never change `audio_completeness=not_verified`.
- Enhancement merges by Gate-checked interval replacement (ADR 0045): inside
  the user-specified interval, ASR cues replace the display layer only after
  passing adoption-style gates; on failure the original subtitle cues stay
  with a recorded reason. Original cues remain immutable evidence. There is
  no cue-level interleaved mixing.
- Every replacement, rejection, and conflict is written to the correction log
  and the readable correction report.

## Affected-Part Re-Analysis Contract

- After a verbatim or enhanced cue basis is retained, semantic recomputation
  follows ADR 0046: a new immutable text-analysis attempt regenerates only
  affected Parts; unaffected Parts are Carried-forward analysis Parts with
  explicit provenance links to the retained prior report; chapters and the
  collection summary are recomputed from the combined set.
- This requires three new text-analysis capabilities, specified as their own
  atomic tasks: a retained-report loader back to domain objects, an
  affected-Part selector keyed on changed cue identities, and carry-forward
  provenance in the new report.
- Re-analysis never overwrites the prior report and obeys all Phase 6
  contracts unchanged.

## Provenance, Artifacts, And Diagnostics

Each attempt writes an immutable workspace retaining input bindings, capability
and adapter identity, rule versions, execution-resource measurement, raw
output, versioned projections, gate decisions, arbitration decisions,
conflicts, diagnostics, JSON report, and rendered readable artifacts. The
Controlled offline ASR adapter records implementation version, fixed input and
output fixture hashes, and projection-schema hash; it is not a model asset and
cannot earn a real-model quality qualification. A future real model requires a
new attempt whenever its asset, revision, quantization, backend, decoding
configuration, projection schema, or rule versions change.

## Status, Resource, And Recovery Contract

- `complete`: every selected Part has gated transcription evidence and no
  decision is pending.
- `partial`: some Parts failed gates, conflicts remain unresolved, or a
  decision pause exists after validated evidence was retained.
- `failed`: revalidation, whole projection, or execution fails before any
  gated evidence exists.
- Pauses are immutable recorded states: Full-ASR resource confirmation pause,
  Transcription resource-envelope pause (conservative estimate over 24 GiB;
  never silently alters model, quantization, or batch), and
  `model_acquisition_required`.
- Serialized ASR execution: one model completes its evidence record and
  release before another loads; Phase 5 release evidence must be complete
  before a future real ASR model loads.
- No automatic retry. A retry is an explicitly user-started new attempt and
  never overwrites prior evidence.

## Reporting And Language

The JSON report contains capability states, gate and arbitration decisions,
suspicious intervals, conflicts, artifact hashes, statuses, limitations, and
diagnostic pointers. Transcript text keeps its source language, including
mixed Chinese/English; readable report prose defaults to Chinese. Enhanced
reports must state that completeness was not verified; verbatim reports must
state the coverage-check basis for any completeness claim.

## Offline Test Contract

Tests use only hash-pinned synthetic fixtures and Controlled offline ASR
adapters, asserting deterministic contract properties: revalidation drift,
resource-confirmation and envelope pauses, projection validity, coverage and
monotonicity gates, all six detectors, interval-scoped review, arbitration and
retained conflicts, same-model-retry exclusion, verbatim/enhanced semantics
separation, per-cue provenance, gate-failure fallback, affected-Part selection,
carry-forward provenance, immutability, statuses, hashes, and no-side-effect
guarantees (`model_execution`, `model_acquisition`, `network_access`,
`outputs_publication` all `not_attempted`).

## Out Of Scope

- Model acquisition, installation, download, runtime setup, or real-model
  call — including the phase plan's original "validate candidates locally"
  work items, deferred as a recorded deviation (see Capability And Eligibility
  Contract).
- Real ASR accuracy, language coverage, or memory measurement; CER/WER.
- OCR, visual-text, translation, diarization changes, or new audio analysis.
- RunBundle publication, `outputs/` writes, cleanup, deletion, or a
  `production_validated` state.

## Related Decisions

This specification uses the transcription vocabulary in the
[Context Map](../CONTEXT-MAP.md) and
[Transcription Context](contexts/transcription/CONTEXT.md) and is governed in
particular by
[ADR 0043](adr/0043-introduce-a-transcription-context-with-required-audio-analysis.md),
[ADR 0044](adr/0044-use-deterministic-transcription-arbitration-with-retained-conflicts.md),
[ADR 0045](adr/0045-use-gate-checked-interval-replacement-for-enhanced-subtitles.md), and
[ADR 0046](adr/0046-recompute-affected-parts-with-carried-forward-analysis.md),
with the offline verification boundary inherited from
[ADR 0036](adr/0036-keep-phase-5-model-capabilities-provider-neutral.md) and
[ADR 0037](adr/0037-verify-phase-5-with-controlled-offline-adapters.md).
