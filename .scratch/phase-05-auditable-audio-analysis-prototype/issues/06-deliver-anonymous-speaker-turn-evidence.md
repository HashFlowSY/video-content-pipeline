# 06 -- Deliver anonymous speaker-turn evidence

**What to build:** A user can receive calibrated anonymous speaker-turn structure
for each Part without real-person identity claims. The report preserves overlap,
exposes VAD disagreement, and limits role candidates to text- or metadata-backed
evidence.

**Blocked by:** 04 -- Deliver VAD evidence and caption-gap risks.

**Status:** ready-for-agent
**Labels:** ready-for-agent

- [ ] Formal SpeakerTurns use Part-local anonymous labels, RawPtsTime intervals, and calibration-backed confidence; no cross-Part, cross-run, voiceprint, or real-name association is emitted.
- [ ] Overlapping SpeakerTurns remain separate evidence, while candidates that overlap non-speech or indeterminate VAD retain `diarization_vad_conflict` and are not repaired or published.
- [ ] Role candidates require cited subtitle text or explicit user metadata and never arise from voice characteristics or raw diarization groups.
