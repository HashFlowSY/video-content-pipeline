# 04 -- Deliver VAD evidence and caption-gap risks

**What to build:** A user can obtain calibrated VAD evidence for every selected
Part through `vcp analyze-audio`, including Parts without a Primary subtitle
track. The report distinguishes speech, non-speech, and uncertainty from
subtitle coverage without creating a transcript.

**Blocked by:** 03 -- Introduce capability and calibration evidence.

**Status:** ready-for-agent
**Labels:** ready-for-agent

- [ ] Formal Voice activity intervals completely partition known usable audio in RawPtsTime into `speech_likely`, `non_speech`, or `indeterminate`; missing, rounded, or undecidable segments remain `indeterminate`.
- [ ] The report retains all uncovered-speech risk evidence, separately reports `audio_state_indeterminate`, and elevates only continuous duration-qualified risk without silently discarding short evidence.
- [ ] Long-silence evidence derives only from continuous calibrated non-speech, never caption gaps, coverage gaps, or indeterminate audio.
