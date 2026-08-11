# 04 -- Deliver VAD evidence and caption-gap risks

**What to build:** A user can obtain calibrated VAD evidence for every selected
Part through `vcp analyze-audio`, including Parts without a Primary subtitle
track. The report distinguishes speech, non-speech, and uncertainty from
subtitle coverage without creating a transcript.

**Blocked by:** 03 -- Introduce capability and calibration evidence.

**Status:** resolved
**Labels:** ready-for-agent

- [x] Formal Voice activity intervals completely partition known usable audio in RawPtsTime into `speech_likely`, `non_speech`, or `indeterminate`; missing, rounded, or undecidable segments remain `indeterminate`.
- [x] The report retains all uncovered-speech risk evidence, separately reports `audio_state_indeterminate`, and elevates only continuous duration-qualified risk without silently discarding short evidence.
- [x] Long-silence evidence derives only from continuous calibrated non-speech, never caption gaps, coverage gaps, or indeterminate audio.

## Comments

2026-08-10: Implemented calibrated controlled-adapter VAD evidence through
`vcp analyze-audio`. The report records the selected retained audio stream,
requires a unique calibrated VAD candidate, rejects incomplete or out-of-coverage
projection segments, and partitions only known usable audio with exact RawPtsTime
boundaries. Caption gaps yield retained uncovered-speech or
`audio_state_indeterminate` evidence without a transcript; only calibrated
duration thresholds affect risk elevation and continuous non-speech yields long
silence. Tests cover coverage gaps, short risks, absent Primary subtitle coverage,
and the CLI report contract. No model, dependency, source media, network, or
`outputs/` publication action occurred. Ruff, strict Mypy, and the complete 147-test
suite passed. Final review found that the Phase 3 RunPlan does not retain an
inspection-evidence fingerprint, so Phase 5 cannot prove that mutable retained
PlanReport coverage/stream evidence has not changed before publishing formal
VAD evidence. Ticket completion remains pending that prerequisite boundary.

2026-08-11: The later source/subtitle revalidation change does not complete
this prerequisite. `_matches_confirmed_plan` does not bind retained inspection
evidence to the RunPlan, and `_revalidate_analysis_inputs` does not independently
revalidate it. VAD currently compares a projection against hashes recomputed
from the currently loaded PlanReport, not an immutable confirmed inspection
record. Ticket 04 remains open until Ticket 02's derivative boundary and this
PlanReport inspection-evidence binding are complete.

2026-08-11: Resolved by `c99914b`. The confirmed RunPlan now retains a
per-Part inspection-evidence fingerprint and `vcp analyze-audio` rejects any
PlanReport whose inspection evidence differs before selecting streams or
publishing formal VAD evidence. The explicit drift CLI case and full 155-test
verification passed, with Ruff, formatter, and strict Mypy also passing.
