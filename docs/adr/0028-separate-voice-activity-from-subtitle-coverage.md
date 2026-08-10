# Separate voice activity from subtitle coverage

Phase 5 represents VAD output as Voice activity intervals classified as
`speech_likely`, `non_speech`, or `indeterminate`. The pipeline derives
uncovered-speech risk only by comparing those intervals with Primary subtitle
track coverage, so a caption gap is never silently labeled as silence or text.
Intervals use RawPtsTime half-open boundaries and must be contained in usable
audio DecodedIntervals; coverage gaps, missing audio, and undecidable ranges
remain `indeterminate`.

Every non-empty `speech_likely` interval outside Primary subtitle coverage is
retained as uncovered-speech risk evidence. Only a versioned, calibrated
duration threshold elevates a continuous interval to a material report risk or
ASR-planning recommendation; sub-threshold evidence remains retained.
An overlap between missing subtitle coverage and `indeterminate` audio is an
`audio_state_indeterminate` risk only; it never becomes a speech, silence, or
ASR conclusion.

Long-silence evidence derives only from a continuous calibrated `non_speech`
interval above a versioned, calibrated duration threshold. Indeterminate audio,
audio coverage gaps, and missing subtitles cannot join or extend it.

## Considered Options

- Separate audio state and caption coverage: accepted because they are distinct
  evidence sources with independent uncertainty.
- A combined VAD/caption state: rejected because it would conflate missing
  captions, silence, and ambiguous audio evidence.
- Container-duration or video-derived silence: rejected because only decoded
  audio coverage can support an audio-state conclusion.
- Discarding short uncovered-speech intervals: rejected because reporting
  thresholds affect prominence, not whether the underlying evidence exists.
- Treating indeterminate audio as speech or silence: rejected because an
  uncertain VAD result cannot establish either conclusion.
- Joining silence across caption gaps or unknown audio: rejected because a long
  silence must remain an audio conclusion supported by continuous evidence.
