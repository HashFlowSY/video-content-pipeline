# Transcription Context

This Context owns ASR-derived text evidence: full verbatim transcription,
interval-scoped local enhancement, suspicious-interval detection, independent
review, and deterministic arbitration. It depends on subtitle evidence and —
as a required dependency — audio-analysis evidence; it feeds text-analysis
with a changed cue basis. Operational mechanics and exact thresholds remain in
the linked specifications and ADRs.

Relevant global decisions include
[ADR 0036](../../adr/0036-keep-phase-5-model-capabilities-provider-neutral.md),
[ADR 0037](../../adr/0037-verify-phase-5-with-controlled-offline-adapters.md),
[ADR 0042](../../adr/0042-use-context-map-and-domain-owned-glossaries.md),
[ADR 0043](../../adr/0043-introduce-a-transcription-context-with-required-audio-analysis.md),
[ADR 0044](../../adr/0044-use-deterministic-transcription-arbitration-with-retained-conflicts.md), and
[ADR 0045](../../adr/0045-use-gate-checked-interval-replacement-for-enhanced-subtitles.md).

## Language

### Capabilities and eligibility

**Transcription capability contract**:
The provider-neutral pair of ASR capabilities: `asr_primary` for full
transcription and `asr_review` for independent interval review.
_Avoid_: model-specific pipeline

**Independent-model review requirement**:
The rule that review evidence must come from a different eligible model; a
same-model retry is a recovery attempt, never independent review.
_Avoid_: retry-as-review

**Model-acquisition-required transcription result**:
The recorded outcome when no eligible ASR capability is locally available.
_Avoid_: implicit download

**Controlled offline ASR adapter**:
The fixed substitute capability used to verify transcription contracts without
claiming real-world model quality.
_Avoid_: synthetic accuracy qualification

### Artifacts and provenance

**Verbatim transcription artifact**:
The transcript artifact class producible only by a complete full-ASR run.
_Avoid_: enhanced-as-verbatim

**Enhanced subtitle artifact**:
An interval-scoped subtitle artifact merging subtitle-track and ASR cues with
per-cue provenance; it never claims full verbatim completeness.
_Avoid_: silent cue mixing

**Cue-level transcription provenance**:
The per-cue record of source (`subtitle_track` or `asr`), candidates, and gate
decisions.
_Avoid_: track-level attribution

**Audio-completeness upgrade**:
The rule that only a complete verbatim transcription may change
`audio_completeness` from `not_verified`.
_Avoid_: enhancement-claimed completeness

**Transcription attempt provenance**:
The identity record linking transcription candidates to their inputs, models
or adapters, and rule versions.
_Avoid_: unrecorded variance

**Immutable transcription workspace**:
The immutable evidence set associated with one transcription or enhancement
attempt.
_Avoid_: mutable transcript folder

### Suspicion, review, and arbitration

**Suspicious interval**:
A time interval flagged by versioned deterministic detectors as requiring
independent review.
_Avoid_: model-felt doubt

**Versioned suspicion detection rules**:
The versioned detector set (VAD coverage, confidence, repetition, language
switching, numbers/entities, and coverage checks) with conservative defaults
and explicit calibration-required marks.
_Avoid_: tuned-in-place threshold

**Deterministic transcription arbitration**:
Versioned preference rules deciding between primary and review candidates;
when no rule decides, no candidate is chosen.
_Avoid_: majority vote

**Unresolved transcription conflict**:
A retained disagreement in which the primary text stands, both candidates
remain evidence, and the interval is marked review-needed.
_Avoid_: auto-resolved truth

### Enhancement

**Gate-checked interval replacement**:
The enhanced merge rule that ASR cues replace the display layer of a
user-specified interval only after passing adoption-style gates; on failure
the original cues stay with a recorded reason.
_Avoid_: interleaved merge

### Execution boundaries

**Explicit transcription command boundary**:
The domain boundary that starts or resumes one transcription or enhancement
attempt from retained evidence.
_Avoid_: background transcription

**Full-ASR resource confirmation pause**:
The pause requiring explicit confirmation of the resource plan before a full
ASR run may execute on a subtitle-unavailable source.
_Avoid_: automatic ASR trigger

**Transcription resource-envelope pause**:
A domain state in which a planned transcription exceeds its approved resource
envelope.
_Avoid_: silent quantization change

**Serialized ASR execution**:
A sequencing relationship in which one ASR execution completes its evidence
record and release before another model loads.
_Avoid_: concurrent large-model load
