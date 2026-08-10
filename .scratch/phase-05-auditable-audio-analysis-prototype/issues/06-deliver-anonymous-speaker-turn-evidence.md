# 06 -- Deliver anonymous speaker-turn evidence

**What to build:** A user can receive calibrated anonymous speaker-turn structure
for each Part without real-person identity claims. The report preserves overlap,
exposes VAD disagreement, and limits role candidates to text- or metadata-backed
evidence.

**Blocked by:** 04 -- Deliver VAD evidence and caption-gap risks.

**Status:** resolved
**Labels:** ready-for-agent

- [x] Formal SpeakerTurns use Part-local anonymous labels, RawPtsTime intervals, and calibration-backed confidence; no cross-Part, cross-run, voiceprint, or real-name association is emitted.
- [x] Overlapping SpeakerTurns remain separate evidence, while candidates that overlap non-speech or indeterminate VAD retain `diarization_vad_conflict` and are not repaired or published.
- [x] Role candidates require cited subtitle text or explicit user metadata and never arise from voice characteristics or raw diarization groups.

## Comments

2026-08-10: Implemented controlled, calibrated diarization evidence through the
offline `vcp analyze-audio` contract. Formal turns use report-local anonymous
labels, retain legal overlaps, and exclude non-speech or indeterminate VAD
conflicts from publication while recording `diarization_vad_conflict` evidence.
Role candidates require exact retained subtitle citations or explicit
`--role-metadata` assignments retained as hash-recorded workspace evidence.
Diarization requires an explicit candidate selection; recovery from that pause
uses `vcp resume-audio-analysis` with the retained partial report ID. The full
suite (150), Ruff, formatter, and strict Mypy passed with controlled fixtures
only: no model download/execution, network request, user-media access, or
output publication occurred.
