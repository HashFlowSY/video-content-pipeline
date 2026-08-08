# 04 -- Derive StreamCoverage from DecodedIntervals

Category: enhancement
Status: resolved
Labels: enhancement

**What to build:** A verifiable coverage result derived only from observed
`DecodedInterval` evidence, including an exact outer envelope, visible internal
gaps, and fail-closed indeterminate coverage.

**Blocked by:** 03 -- Project FFprobe evidence without fallback.

- [x] `StreamCoverage` uses the exact minimum observed start and maximum
  observed end, while internal discontinuities remain diagnostics.
- [x] A required unknown boundary yields `coverage_indeterminate` rather than a
  metadata-duration or text-derived substitute.
- [x] Tests cover differing stream starts, priming-like offsets, internal
  gaps, incomplete endpoints, and contradictory duration metadata.

## Comments

2026-08-09: Implemented the internal `video_content_pipeline.coverage`
boundary. `derive_stream_coverage` accepts only `DecodedInterval` evidence;
complete intervals form an exact half-open outer envelope, and sorted internal
discontinuities remain separately recorded gaps. Negative starts are retained.
Missing starts, missing ends, and an empty interval set return no coverage with
a structured `coverage_indeterminate` diagnostic. Duration metadata is not a
coverage input and is rejected by the public API. TDD first showed the expected
missing-module import failure, then 5 focused unit tests passed. Ruff, strict
Mypy, and the full suite passed. No FFmpeg, FFprobe, fixture, package,
download, model, paid API, user-media, or CLI action occurred. Post-
implementation standards review reported no findings; specification review
found and corrected the duration-metadata test gap.
