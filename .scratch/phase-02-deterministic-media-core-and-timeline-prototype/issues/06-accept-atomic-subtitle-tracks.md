# 06 -- Accept atomic subtitle tracks

Category: enhancement
Status: ready-for-agent
Labels: enhancement, ready-for-agent

**What to build:** A complete SRT and VTT subtitle-evidence path that creates
immutable `RawCue` and losslessly normalized `NormalizedCue` records only when
the whole subtitle track is valid against determinate stream coverage.

**Blocked by:** 04 -- Derive StreamCoverage from DecodedIntervals.

- [ ] Valid SRT and VTT inputs retain source text, exact time, source ordinal,
  Part identity, and track identity in immutable evidence records.
- [ ] Any syntax, duration, ordering, or source-bound validation failure marks
  the complete track `invalid`; no partial recovery or output is emitted.
- [ ] Tests cover valid tracks, malformed input, invalid interval boundaries,
  out-of-coverage cues, indeterminate coverage, and lossless normalization.
