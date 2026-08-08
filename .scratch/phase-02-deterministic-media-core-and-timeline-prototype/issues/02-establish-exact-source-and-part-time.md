# 02 -- Establish exact source and Part time

Category: enhancement
Status: resolved
Labels: enhancement

**What to build:** A verifiable library behavior that represents exact rational
time, preserves signed `RawPtsTime`, uses half-open intervals, and translates
valid source boundaries into non-negative `PartRelativeTime` without replacing
source evidence.

**Blocked by:** 01 -- Approve Phase 2 execution envelope.

- [x] Exact comparison, arithmetic, and translation remain stable without
  floating-point accumulation.
- [x] Negative raw PTS remains valid evidence, while Part-relative subtitle
  coordinates derive from the observed Part coverage start and remain
  non-negative.
- [x] Unit evidence covers signed and nonzero PTS, positive and invalid
  half-open intervals, and traceability from derived time back to raw time.

## Comments

2026-08-08: Implemented `video_content_pipeline.timecode` with immutable exact
rational time, signed `RawPtsTime`, evidence-bearing `PartCoverageStart`,
half-open interval validation, and non-negative `PartRelativeTime` derived
only from retained source evidence. The time module exposes structured
`TimeValidationError.reason` values for invalid interval and negative
Part-relative conditions. The approved checks passed: 8 time-unit tests,
Ruff lint and format checks, and strict Mypy. Both standards and specification
review axes reported no remaining findings. No FFmpeg, FFprobe, fixture,
package, download, or user-media action occurred.
