# 05 -- Assemble compact CollectionVirtualTime

Category: enhancement
Status: resolved
Labels: enhancement

**What to build:** A collection-facing mapping that concatenates ordered Part
coverage into contiguous `CollectionVirtualTime` while preserving each Part's
authoritative raw coordinate and hard boundary.

**Blocked by:** 04 -- Derive StreamCoverage from DecodedIntervals.

- [x] The first Part begins at collection virtual time zero and every later
  Part begins at the previous Part's exact coverage endpoint.
- [x] Encoder-origin PTS gaps and unrelated container duration do not create
  artificial collection gaps.
- [x] Tests cover nonzero and negative source PTS origins, compact mapping, and
  the prohibition on cross-Part cue merging.

## Comments

2026-08-09: Implemented the internal `video_content_pipeline.timeline`
boundary. `CollectionTimeline` concatenates ordered `TimelinePart` coverage
spans from zero, so absolute and encoder-origin PTS gaps do not become
collection gaps. Each `CollectionVirtualTime` retains its `part_id` and
`PartRelativeTime`, including the authoritative `RawPtsTime` and time base;
matching virtual endpoints on adjacent Parts remain distinct source-owned
coordinates. TDD first showed the expected missing-module import failure, then
3 focused unit tests passed. Ruff and strict Mypy passed; the full suite passed
27 tests. No FFmpeg, FFprobe, fixture, package, download, model, paid API,
user-media, or CLI action occurred.
