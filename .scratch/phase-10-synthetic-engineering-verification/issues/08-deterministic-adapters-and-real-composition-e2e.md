# 08 — Wire deterministic model adapters into the real composition and run end to end

**What to build:** The first true offline end-to-end `vcp run`. Build
deterministic substitute model adapters (ADR 0037 lineage, in
`tests/support/`) for every model capability seam the per-phase functions
require — primary ASR, second ASR, text model, OCR — producing
content-derived, hash-seeded outputs (same input bytes → same output,
different inputs → different output; no randomness). Through the existing
`_composition_factory` seam, inject a **production `RunComposition`**
wired to the real per-phase functions, real ffmpeg/ffprobe, and the real
filesystem, over ticket-03 fixtures; the adapters are the only fakes, and
production code gains no test modes. Extend Phase 9's deliberately
conservative evidence/report gatherers exactly as far as needed for the
published bundle's core content artifacts (subtitles, transcript,
content-report, segments) to be VALID — no broader reconstruction
(grilling Q7). Prove at minimum the subtitle-first branch to
`complete`/published with `vcp verify` green; other branches are ticket 10.

**Blocked by:** 03
**Status:** done
**Labels:** ready-for-agent

- [x] Adapters are deterministic (double-run byte-identical bundle digest) —
      `test_adapters_are_deterministic_double_run` runs the whole pipeline in two
      fresh roots over one shared fixture and asserts the core content artifacts'
      digests are byte-identical (audit docs legitimately vary by run identity)
- [x] Composition is the production `RunComposition`; only model adapters faked —
      the e2e drives `build_run_composition` + `execute_confirmed_run` with the
      default real `StageFunctions`; the only seam is `tests/support/model_adapters`
- [x] Real ffmpeg/ffprobe execute inside the run — real ffprobe at plan
      inspection, real ffmpeg for subtitle demux and analysis-audio extraction
      (asserted: a real `.wav` derivative lands under `work/`)
- [x] Published bundle core artifacts VALID; `vcp verify` green — subtitles
      (source+readable, per-Part and collection), transcript.source, content-report
      and segments are all VALID; `verify_published_bundle` returns verified with
      no discrepancies
- [x] No production test modes introduced; gatherer extensions minimal and listed
      — the four gatherer extensions are docstring-listed in `_gather_evidence`;
      the three production changes are genuine correctness fixes (below)
- [x] Suite green within budget — full suite 1288 passed in ~11s; ruff + mypy clean

## Comments

Delivered on top of `d934e2b`. `tests/integration/test_phase_10_synthetic_e2e.py`
is the first true offline `vcp run`: the production composition + real per-phase
functions + real ffmpeg/ffprobe over the ticket-03 subtitle-first fixture, with
`tests/support/model_adapters.py` (content-derived, hash-seeded controlled audio
and text adapters, ADR 0037 lineage) as the only fake. Every bound value is
derived from the run's own real inputs (the plan's ffprobe structural/coverage
SHAs, the subtitle workspace's cue content), so the adapters are deterministic.

The first real run exposed three genuine production gaps, fixed here (no test
modes):

1. `run_composition._invoke_transcription` now completes as a no-op in
   subtitle-first mode when the subtitle report parsed cleanly, shows no ASR
   handoff, and no upgrade was requested — the transcription context's own
   "a subtitle-priority run never triggers ASR automatically" contract, which the
   composition previously violated by always invoking `transcribe` (tripping its
   precondition guard and failing the run). Full-ASR, `--upgrade-all`, and an
   unparsed report all still fall through to `transcribe`.
2. `audio_derivation._exact_timestamp` now emits decimal seconds instead of a
   `numerator/denominator` rational; FFmpeg's `-ss`/`-t` reject the rational, so
   the analysis-audio extraction (never run against real FFmpeg before, ADR 0037)
   was simply wrong. Whole seconds exact, fractional part expanded exactly, capped
   at FFmpeg's microsecond resolution.
3. `run_composition._gather_evidence` extended exactly as far as the acceptance
   requires (grilling Q7): per-Part and single-Part-collection subtitles in the
   mode's bases, a plain source transcript from the cues, and content-report +
   segments from the text-analysis report's verified segments. correction-log is
   unchanged from Phase 9.

Fixture note: ticket-03's `synthetic_fixtures` audio moved to FLAC @ 32 kHz with
`-bitexact` (RECIPES_VERSION → 2). At 48 kHz AAC the decoded packet coverage was
scattered with ~1 ms gaps (the 21.33 ms frame does not tile Matroska's ms
timebase) and started at a negative AAC pre-roll the extractor's `-ss` could not
express — both made the audio unprocessable end to end and were invisible to a
purely structural probe. FLAC @ 32 kHz tiles the ms timebase gap-free, starts at
zero, and `-bitexact` makes generation byte-reproducible. The five branches still
probe as declared.

Two-axis code review (Standards + Spec) ran clean of hard/spec-correctness issues.
Applied quality fixes: replaced stringly `state.value == "valid"` with the
`CandidateState.VALID` enum and shared one `_valid_candidates_by_source` helper
between the subtitle and transcript gatherers.
