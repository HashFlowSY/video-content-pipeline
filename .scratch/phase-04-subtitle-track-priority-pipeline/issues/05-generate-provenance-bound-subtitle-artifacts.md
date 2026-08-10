# 05 -- Report partial collections and ASR handoff

**What to build:** A multi-Part user receives a truthful partial subtitle
collection with completed Parts, coverage and risk reporting, and an explicit
ASR-planning handoff for every unavailable Part.

**Blocked by:** 02 -- Produce common-format readable subtitles; 03 -- Resolve ambiguous subtitle-track selection explicitly; 04 -- Preserve bounded subtitle-processing failures.

**Status:** resolved
**Labels:** ready-for-agent

- [x] Completed Parts retain CollectionVirtualTime and candidate artifacts even
  when another Part is unavailable.
- [x] Caption-time coverage counts overlapping cue time once and always reports
  `audio_completeness=not_verified` with source and processing risks.
- [x] An unavailable Part has no invented cue, silence label, ASR estimate,
  model action, or RunBundle; it reports only ASR-planning handoff evidence.

## Comments

2026-08-10: Added immutable per-Part subtitle reports to the `vcp subtitles`
candidate report. Each report retains a compact CollectionVirtualTime span,
exact caption-time coverage with overlap counted once, and
`audio_completeness=not_verified`. A collection with completed and unavailable
Parts returns `partial`; unavailable Parts retain their source diagnostics and
only a `subtitle_unavailable_requires_asr_plan` handoff, with no generated cue,
silence marker, ASR estimate, model action, or RunBundle. Offline verification
passed: 135 tests, Ruff, formatter check, and strict Mypy.
