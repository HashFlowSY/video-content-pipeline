# 06 -- Accept atomic subtitle tracks

Category: enhancement
Status: resolved
Labels: enhancement

**What to build:** A complete SRT and VTT subtitle-evidence path that creates
immutable `RawCue` and losslessly normalized `NormalizedCue` records only when
the whole subtitle track is valid against determinate stream coverage.

**Blocked by:** 04 -- Derive StreamCoverage from DecodedIntervals.

- [x] Valid SRT and VTT inputs retain source text, exact time, source ordinal,
  Part identity, and track identity in immutable evidence records.
- [x] Any syntax, duration, ordering, or source-bound validation failure marks
  the complete track `invalid`; no partial recovery or output is emitted.
- [x] Tests cover valid tracks, malformed input, invalid interval boundaries,
  out-of-coverage cues, indeterminate coverage, and lossless normalization.

## Comments

2026-08-09: Implemented `video_content_pipeline.subtitles` with atomic SRT
and WebVTT parsing, immutable `RawCue` and `NormalizedCue` evidence, exact
millisecond intervals, Part/track provenance, fail-closed coverage validation,
and lossless token-preserving normalization. Invalid syntax, duration,
format, source-bound, internal-gap, or indeterminate-coverage evidence returns
an invalid track with the original source retained and no partial cues.
Focused subtitle tests (10), Ruff, strict Mypy, and the full suite (37) passed.
No FFmpeg, FFprobe, fixture, package, download, model, paid API, user media,
or CLI action occurred.
