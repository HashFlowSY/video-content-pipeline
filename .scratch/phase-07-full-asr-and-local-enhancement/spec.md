# Phase 7: Full ASR Transcription and Local Enhancement

## Domain routing

Begin with the [Context Map](../../CONTEXT-MAP.md), then read [Media
Foundation](../../docs/contexts/media-foundation/CONTEXT.md), [Source
Planning](../../docs/contexts/source-planning/CONTEXT.md),
[Subtitles](../../docs/contexts/subtitles/CONTEXT.md), [Audio
Analysis](../../docs/contexts/audio-analysis/CONTEXT.md), and
[Transcription](../../docs/contexts/transcription/CONTEXT.md). [Text
Analysis](../../docs/contexts/text-analysis/CONTEXT.md) participates through
Affected-Part re-analysis. Audio analysis is a required transcription
dependency (ADR 0043).

Type: enhancement
Status: ready-for-agent
Labels: ready-for-agent
Phase: 7
Published: 2026-08-15

## Problem Statement

After Phase 6, a user with a subtitle-unavailable source gets no transcript at
all, and a user who reviewed a subtitle-priority result and found missing or
wrong passages has no way to upgrade it. The pipeline can already hand off a
`subtitle_unavailable_requires_asr_plan` diagnostic, but nothing consumes it.
ASR text must enter the evidence system without breaking its guarantees: never
triggered silently, never mixed with subtitle cues at unauditable granularity,
never claiming audio completeness it cannot prove, and never resolved to
"truth" by a vote between models.

## Solution

Two explicit command boundaries. `vcp transcribe` runs the full-ASR path for
subtitle-unavailable sources (after an explicit resource-plan confirmation) or
for an explicit whole-selection upgrade, producing Verbatim transcription
artifacts after suspicious-interval detection, independent second-ASR review,
and deterministic arbitration. `vcp enhance` runs the local path on
user-named Parts, time ranges, or problem cues, producing Enhanced subtitle
artifacts by gate-checked interval replacement with per-cue provenance. After
either changes the cue basis, a new text-analysis attempt regenerates only the
affected Parts and carries the rest forward with provenance, recomputing
chapters and collection summaries. All contracts are proven offline with
Controlled offline ASR adapters and hash-pinned synthetic fixtures; no model
is downloaded or executed in this phase.

## User Stories

1. As a pipeline user with a subtitle-unavailable source, I want a full ASR
   transcription path, so that I can obtain a transcript at all.
2. As a pipeline user, I want the full ASR run to pause at a resource
   confirmation showing the plan's time, memory, and disk envelope before
   executing, so that I never start an unbounded heavy run by accident.
3. As a pipeline user who reviewed a subtitle-priority result, I want to
   explicitly upgrade all selected Parts to full ASR, so that I can replace an
   untrustworthy subtitle track wholesale.
4. As a pipeline user, I want subtitle-priority runs to never trigger ASR
   automatically, so that heavy model work only happens when I ask for it.
5. As a pipeline user, I want to name a Part, a time range, or specific
   problem cues for local enhancement, so that I can fix exactly the passages
   I found wrong without paying for a full re-transcription.
6. As a pipeline user, I want enhanced output to keep every untouched cue
   exactly as it was, so that a local fix cannot quietly degrade the rest.
7. As a pipeline user, I want each enhanced cue labeled `subtitle_track` or
   `asr`, so that I always know which text came from where.
8. As a pipeline user, I want ASR candidates that fail the timing gates to be
   rejected in favor of the original cues, with the reason recorded, so that a
   bad enhancement can never be worse than no enhancement.
9. As a pipeline user, I want verbatim artifacts only from a complete full-ASR
   run that passed coverage checks, so that a "verbatim" label actually means
   what it says.
10. As a pipeline user, I want enhanced artifacts to state that full
    completeness was not verified, so that I am never misled about coverage.
11. As a pipeline user, I want suspicious intervals found automatically during
    full ASR (silence-mismatch, low confidence, repetition, language switches,
    numbers/entities, coverage), so that likely errors are located for me.
12. As a pipeline user, I want a second, independent ASR model to re-transcribe
    only the suspicious intervals by default, so that review cost stays
    proportional to risk.
13. As a pipeline user, I want to explicitly request full-length second-ASR
    review when I distrust the whole transcript, so that the default economy
    does not cap my options.
14. As a pipeline user, I want disagreements between the two models settled by
    versioned deterministic rules — and left explicitly unresolved when the
    rules cannot decide — so that no model vote silently invents truth.
15. As a pipeline user, I want unresolved conflicts marked `review-needed`
    with both candidates retained, so that I can adjudicate them myself later.
16. As a pipeline user, I want chapters and collection summaries recomputed
    after an enhancement, so that my reading entry point reflects the fixed
    text.
17. As a pipeline user with a long multi-Part collection, I want only the
    affected Parts re-analyzed and the rest carried forward, so that a small
    fix does not re-run the text model over hours of untouched content.
18. As a pipeline user, I want every transcription or enhancement attempt to
    create a new immutable workspace, so that no attempt ever overwrites
    prior evidence.
19. As a pipeline user, I want to resume a paused attempt only by naming the
    report and an explicit decision, so that nothing continues without my
    say-so.
20. As a pipeline user, I want a run whose conservative memory estimate
    exceeds the 24 GiB envelope to pause rather than silently switch model,
    quantization, or batch size, so that quality trade-offs are always mine.
21. As a pipeline user, I want mixed Chinese/English speech transcribed in its
    original mixed form, so that source language is never rewritten.
22. As a pipeline user, I want machine-readable JSON reports beside readable
    renderings for every command, so that I can script against the results.
23. As an auditor, I want every replacement, rejection, arbitration decision,
    and conflict written to the correction log with rule versions, so that
    any published word traces to a recorded decision.
24. As an auditor, I want raw model output retained as restricted diagnostic
    evidence and excluded from formal artifacts, so that I can inspect
    failures without them leaking into results.
25. As an auditor, I want carried-forward Parts to link explicitly to their
    source report, so that reused analysis is distinguishable from fresh
    analysis.
26. As an auditor, I want the offline verification report to assert that no
    model execution, model acquisition, network access, or output publication
    was attempted, so that the phase's claims are machine-checkable.
27. As a future real-model integrator, I want provider-neutral `asr_primary`
    and `asr_review` capability contracts with registry-based eligibility
    (license, pinned revision, hashes, offline runtime, resource envelope),
    so that swapping in a real model changes configuration, not semantics.
28. As a future real-model integrator, I want a same-model retry recorded as a
    recovery attempt and never as independent review, so that review
    independence cannot be faked.
29. As a future real-model integrator, I want every suspicion threshold marked
    `calibration_required` with conservative defaults, so that real-world
    calibration has an explicit place to land.
30. As a pipeline user with no eligible ASR capability available, I want an
    immutable `model_acquisition_required` report instead of an implicit
    download, so that acquisition remains a separately authorized decision.
31. As a downstream text-analysis consumer, I want the enhanced or verbatim
    cue basis to flow through the same revalidation and citation rules as
    subtitle cues, so that Phase 6 guarantees survive the upgrade.

## Implementation Decisions

- A new `transcription` bounded context owns ASR-derived text evidence. It
  requires `subtitles` and `audio-analysis` (ADR 0043) and feeds
  `text-analysis` with a changed cue basis. Vocabulary lives in the
  Transcription Context; this spec uses it throughout.
- Two separate start commands, each with a resume counterpart taking a report
  ID and an explicit `--decision`, mirroring the Phase 5/6 CLI pattern.
  `transcribe` takes the plan, subtitle report, and required audio-analysis
  report; `enhance` takes the plan and subtitle report, with the
  audio-analysis report optional and scope given only by user-named Parts,
  ranges, or cues. Only `transcribe` can produce verbatim artifacts.
- Preconditions: `transcribe` requires a retained
  `subtitle_unavailable_requires_asr_plan` handoff or an explicit
  whole-selection upgrade demand, plus the Full-ASR resource confirmation
  pause on subtitle-unavailable sources. `enhance` requires an existing
  subtitle report.
- ASR text enters only through a versioned output projection; an incomplete or
  schema-invalid projection is `model_output_invalid` and fails the attempt.
  Raw output is restricted diagnostic evidence.
- Projected cues pass deterministic timing gates (exact rational times inside
  actual stream coverage, monotonic order, half-open intervals, no processing
  duplication, plausible duration-to-text relation) before becoming candidate
  evidence; rejects carry structured reasons.
- Suspicious intervals come only from Versioned suspicion detection rules:
  six deterministic detectors (VAD coverage, confidence, repetition, language
  switching, numbers/entities, coverage checks) over projections and retained
  audio-analysis evidence, with conservative defaults and
  `calibration_required` marks.
- Arbitration is deterministic (ADR 0044): versioned preference rules decide
  between primary and review candidates; undecided cases keep the primary
  text, retain both candidates, and mark the interval `review-needed`. The
  Independent-model review requirement excludes same-model retries from
  counting as review.
- Enhancement merges by Gate-checked interval replacement (ADR 0045):
  interval-grained replacement of the display layer after gates, original
  cues immutable, per-cue provenance, no cue-level interleaving. Enhancement
  never changes `audio_completeness=not_verified`; only a complete verbatim
  run performs the Audio-completeness upgrade.
- Post-change semantic recomputation follows ADR 0046: a new text-analysis
  attempt regenerates affected Parts, carries unaffected Parts forward with
  provenance links, and recomputes chapters and collection summaries. This
  needs three new text-analysis capabilities: a retained-report loader back
  to domain objects, an affected-Part selector keyed on changed cue
  identities, and carry-forward provenance in the new report.
- Capabilities are provider-neutral (`asr_primary`, `asr_review`) and
  evaluated offline from the model registry with the Phase 5 eligibility
  fields; Qwen3-ASR-1.7B and WhisperKit / Whisper large-v3 are registered as
  research candidates only. Real local-run, language, and memory validation
  is a recorded deviation, deferred to a separately authorized
  model-prototype session before real-world testing.
- Pauses are immutable recorded states: Full-ASR resource confirmation,
  Transcription resource-envelope (over 24 GiB conservative estimate), and
  `model_acquisition_required`. Serialized ASR execution: one model completes
  its evidence record and release before another loads.
- Report statuses: `complete` (all selected Parts gated, nothing pending),
  `partial` (gate failures, unresolved conflicts, or decision pauses after
  retained evidence), `failed` (revalidation, whole projection, or execution
  failure before any gated evidence). No automatic retry.
- Readable prose defaults to Chinese; transcript and cited text keep source
  language, including mixed Chinese/English.

## Testing Decisions

- A good test asserts externally observable contract behavior at the CLI
  boundary — report JSON, statuses, pauses, guarantees, workspace
  immutability, artifact hashes — never internal call sequences or private
  structure.
- Primary seam: the four CLI commands, tested end-to-end with hash-pinned
  synthetic fixtures and Controlled offline ASR adapters, following the prior
  art of the Phase 5 and Phase 6 CLI contract integration tests.
- Second seam (data, existing pattern): the controlled-adapter descriptor with
  fixture hashes; tests build adapter JSON inline in a temporary project root
  exactly as the Phase 6 text-adapter tests do.
- Unit seams only for the deterministic core, per the strict-TDD rule and the
  Phase 6 segmentation/aggregation precedent: timing gates, each of the six
  detectors, arbitration rules, interval replacement, affected-Part
  selection, and report loading are pure functions tested directly.
- Every offline run asserts the guarantees block: `model_execution`,
  `model_acquisition`, `network_access`, `outputs_publication` all
  `not_attempted`.
- Minimum scenario coverage: revalidation drift, both pauses and their
  resumes, no-auto-trigger, projection invalidity, every gate, all six
  detectors, interval-scoped versus explicitly full review, arbitration and
  retained conflicts, same-model-retry exclusion, verbatim/enhanced
  separation, per-cue provenance, gate-failure fallback, carry-forward
  provenance, immutability, mixed Chinese/English, multi-Part collections,
  and subtitle-unavailable sources.
- The phase inventory records machine-checkable `*_confirmed` exit-gate
  booleans mapped to the phase plan's 退出门禁 list.

## Out of Scope

- Model acquisition, installation, download, runtime setup, or any real-model
  execution — including the phase plan's original "validate candidates
  locally" work items, deferred as a recorded deviation.
- Real ASR accuracy, language coverage, memory measurement, or CER/WER.
- OCR, visual-text, translation, diarization changes, new audio analysis, or
  speaker-name inference.
- RunBundle publication, `outputs/` writes, cleanup, deletion, `vcp improve`
  run-level orchestration (Phase 9), and any `production_validated` claim.

## Further Notes

The authoritative phase contract is
[docs/PHASE_07_SPECIFICATION.md](../../docs/PHASE_07_SPECIFICATION.md)
(`approved_for_implementation_planning`, approved 2026-08-15;
`project-state.json` records Phase 7 `implementation_planning`). Atomic
implementation tickets are `issues/01`–`issues/10` in this directory with
dependencies noted per ticket. Governing decisions:
ADR 0043–0046, with the offline boundary inherited from ADR 0036–0037.
Vocabulary owner: the Transcription Context; Affected-Part re-analysis and
Carried-forward analysis Part belong to Text Analysis; the Phase 7 offline
transcription-verification boundary belongs to Media Foundation.
