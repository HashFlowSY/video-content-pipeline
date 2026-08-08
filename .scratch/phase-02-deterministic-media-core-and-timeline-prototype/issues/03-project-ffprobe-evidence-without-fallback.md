# 03 -- Project FFprobe evidence without fallback

Category: enhancement
Status: resolved
Labels: enhancement

**What to build:** A deterministic path from raw FFprobe JSON evidence to a
typed `ProbeProjection`, with `ProbeDocument` preserved for audit and
structured failure when required evidence is unavailable.

**Blocked by:** 02 -- Establish exact source and Part time.

- [x] Raw JSON is retained unchanged as `ProbeDocument`, while known fields
  produce a typed `ProbeProjection` using exact temporal values.
- [x] Unknown fields are tolerated, whereas missing or invalid required fields
  produce `probe_invalid` diagnostics.
- [x] Tests prove that human-readable output, regular expressions, and
  container- or stream-duration guesses never supply a missing required value.

## Comments

2026-08-09: Implemented the internal `video_content_pipeline.probe` boundary.
`ProbeDocument` retains the exact supplied JSON string, while a successful
projection contains only typed stream `index`, `codec_type`, and exact positive
`time_base` values. Unknown fields remain only in raw evidence. Missing or
invalid required evidence returns no projection with `probe_invalid` and
`coverage_indeterminate` diagnostics; duration metadata and human-readable
text are not parsing inputs. TDD showed the expected initial missing-module
failure, then 4 focused tests passed. Ruff, strict Mypy, and the 19-test suite
passed. No FFmpeg, FFprobe, fixture, package, download, model, paid API, user
media, or CLI action occurred.

Post-implementation standards and specification reviews found no issues in
`probe.py` or `test_probe.py`.
