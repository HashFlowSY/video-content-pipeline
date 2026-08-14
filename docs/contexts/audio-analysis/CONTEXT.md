# Audio Analysis Context

This Context owns calibrated audio evidence and derived timing views. It uses
the media clock, source plans, and selected subtitle evidence from its required
dependencies, while retaining every candidate and conflict without rewriting
those sources.
Operational mechanics and exact thresholds remain in the linked specifications
and ADRs.

Relevant global decisions include [ADR 0026](../../adr/0026-keep-adopted-alignment-timing-derived.md),
[ADR 0028](../../adr/0028-separate-voice-activity-from-subtitle-coverage.md), and
[ADR 0039](../../adr/0039-require-deterministic-calibration-evaluation-records.md).

## Language

**Phase 5 analysis partition**:
The scope distinction between evidence that applies to every Part and evidence
that requires a Primary subtitle track.
_Avoid_: subtitle-only audio analysis

**AlignmentCandidate**:
A proposed timing observation associated with an existing Primary subtitle cue.
_Avoid_: aligner transcript

**Adopted alignment timing view**:
An immutable derivative retaining source subtitle text while recording only
cue times accepted after global alignment gates.
_Avoid_: rewritten subtitle

**Cue-level alignment adoption**:
Independent acceptance or rejection of one AlignmentCandidate before the
complete proposed view passes global validity checks.
_Avoid_: track-only adoption

**Alignment-untrusted Part**:
A Part whose proposed adopted view failed a global gate; original cue timing
remains authoritative.
_Avoid_: repaired alignment

**Alignment failure fingerprint**:
An identity describing a recurring alignment failure.
_Avoid_: retry count

**Alignment failure diagnosis**:
An evidence-based explanation of a repeated alignment failure that may remain
inconclusive.
_Avoid_: diagnostic rerun

**Alignment calibration requirement**:
The requirement for a capability-specific calibration profile before candidate
timings can be adopted.
_Avoid_: universal threshold

**Alignment calibration profile**:
Calibration evidence bound to one alignment capability configuration.
_Avoid_: model-name calibration

**Synthetic alignment calibration**:
An alignment profile whose evidence is limited to synthetic media.
_Avoid_: real-source qualification

**Voice activity interval**:
An audio interval classified as speech_likely, non_speech, or indeterminate
without making a transcript claim.
_Avoid_: VAD transcript

**Audio-coverage-constrained VAD**:
Voice activity evidence bounded by usable audio coverage.
_Avoid_: container-duration silence

**VAD calibration requirement**:
The requirement for a capability-specific profile before formal voice
classification or uncovered-speech risk is emitted.
_Avoid_: score-only speech claim

**Uncovered-speech risk evidence**:
The retained intersection of calibrated speech_likely audio and absent Primary
subtitle coverage.
_Avoid_: automatic ASR trigger

**Audio-state-indeterminate risk**:
The retained overlap of absent subtitle coverage and indeterminate audio.
_Avoid_: inferred silence

**Part-local speaker label**:
An anonymous diarization label stable only within one Part.
_Avoid_: global speaker identity

**SpeakerTurn**:
An independently retained diarization interval with a Part-local label and
confidence evidence; overlaps remain separate.
_Avoid_: serialized dialogue

**Role candidate**:
A non-identity role label supported by cited subtitle text or explicit user
metadata.
_Avoid_: voice-inferred role

**Diarization calibration requirement**:
The requirement for a capability-specific profile before formal SpeakerTurns or
role candidates are published.
_Avoid_: provisional identity

**Diarization calibration profile**:
Calibration evidence bound to one diarization capability configuration.
_Avoid_: portable speaker threshold

**Phase 5 heavy-analysis sequence**:
The ordered relationship among VAD, alignment, and diarization evidence.
_Avoid_: concurrent model load

**Model-release-unverified pause**:
A pause state entered when required release evidence is absent.
_Avoid_: forced cleanup

**Resource-envelope-exceeded pause**:
A user-decision state entered before execution when a conservative estimate is
outside the approved resource envelope.
_Avoid_: silent downgrade

**Phase 5 processing authorization**:
Authority to begin analysis after its bound evidence remains valid.
_Avoid_: stale analysis run

**Explicit model acquisition approval**:
User authorization for a proposed model asset after its suitability is
established.
_Avoid_: phase-wide download consent

**Audio analysis workspace**:
The immutable evidence set associated with one audio-analysis attempt.
_Avoid_: model cache

**Partial audio analysis report**:
An immutable report retaining valid completed stages when a later stage pauses or
blocks.
_Avoid_: discarded partial result

**Long-silence evidence**:
A continuous calibrated non_speech interval exceeding the applicable rule.
_Avoid_: caption-gap silence

**Audio analysis clock**:
The RawPtsTime authority for every formal audio-analysis interval and risk.
_Avoid_: normalized audio timeline

**Audio analysis report**:
The immutable machine-readable outcome of one authorized audio-analysis attempt.
_Avoid_: mutable run status

**Model-acquisition-required result**:
The non-acquiring result when no eligible capability is available.
_Avoid_: automatic model fallback

**Forced-alignment candidate**:
A candidate capability for forced alignment.
_Avoid_: selected aligner

**Phase 5 capability contract**:
The provider-neutral evidence obligations for an audio-analysis capability.
_Avoid_: vendor-specific pipeline

**Phase 5 model eligibility**:
The evidence result describing whether a candidate meets the capability's
eligibility requirements.
_Avoid_: downloadable candidate

**Credential-gated model candidate**:
A candidate blocked because access depends on credentials or remote terms.
_Avoid_: login exception

**VAD candidate**:
A candidate capability for voice-activity detection.
_Avoid_: default VAD

**Diarization capability vacancy**:
The state where no diarization candidate satisfies eligibility and no fallback is
substituted.
_Avoid_: implicit diarization fallback

**Model-output projection**:
A structured interpretation of retained native capability output.
_Avoid_: guessed parser result

**Model-output-invalid result**:
The result when complete projection into a capability contract is impossible;
formal evidence is not produced.
_Avoid_: partial projection

**Alignment text-contract violation**:
An aligner output that changes cue text or cardinality instead of proposing times
for existing cues.
_Avoid_: alignment-based ASR

**Order-preserving alignment view**:
An adopted view retaining source cue order while permitting valid overlap.
_Avoid_: de-overlapped subtitles

**Alignment-candidate-rejected cue**:
A cue whose candidate lacks calibrated support, mapping, or usable coverage and
therefore leaves original time authoritative.
_Avoid_: interpolated cue time

**Language-aware alignment duration rule**:
A calibrated, language-supported rule for judging candidate duration against
cue text.
_Avoid_: universal reading speed

**Analysis audio stream**:
The selected usable audio stream used for analysis of a Part.
_Avoid_: mixed analysis audio

**Analysis audio selection record**:
The record binding a Part to its selected analysis stream and evidence.
_Avoid_: bare stream index

**Analysis audio derivative**:
A derived representation of one selected analysis stream for analysis.
_Avoid_: library-default waveform

**Analysis audio derivation toolchain**:
The declared means by which an Analysis audio derivative is produced.
_Avoid_: model-owned decoder

**Derivative-to-source time mapping**:
The exact mapping from derivative coordinates back to RawPtsTime.
_Avoid_: float-only timing

**Complete VAD partition**:
The partition of known usable audio into exactly one formal voice state.
_Avoid_: omitted-audio silence

**Diarization-VAD conflict**:
A retained disagreement between a diarization candidate and calibrated audio
state that blocks formal publication of that turn.
_Avoid_: silent conflict repair

**Alignment-VAD conflict**:
A retained alignment candidate overlap with calibrated non_speech that rejects
the candidate while preserving original cue time.
_Avoid_: warning-only conflict

**Calibration evaluation record**:
A retained evaluation that supports one capability calibration profile.
_Avoid_: informal threshold test

**Calibration-failed result**:
The retained outcome when a candidate fails calibration; thresholds are not
automatically tuned or retried.
_Avoid_: auto-calibrated model
