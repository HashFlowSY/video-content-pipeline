# 07 -- Produce ordered PresentationCue exports

Category: enhancement
Status: resolved
Labels: enhancement

**What to build:** A deterministic presentation and export path for accepted
subtitle evidence that preserves valid cue overlap, exposes stable order, and
serializes exact time outward to parseable millisecond SRT and VTT intervals.

**Blocked by:** 05 -- Assemble compact CollectionVirtualTime; 06 -- Accept atomic subtitle tracks.

- [x] `PresentationCue` remains separately immutable from `RawCue` and
  `NormalizedCue`, with no source-text mutation in the export path.
- [x] Cue order is `(start, end, source_ordinal)` and overlapping intervals
  remain valid evidence rather than being trimmed, merged, or shifted.
- [x] Export floors exact starts and ceils exact ends, including positive
  sub-millisecond source intervals, while retaining the serialization envelope
  as derived rather than authoritative time.

## Comments

2026-08-09: Implemented immutable `PresentationCue` records with complete
source-token provenance, derived `SerializationEnvelope` values, stable
overlap-preserving export order, and parseable outward-millisecond SRT/VTT
serialization. TDD covered immutable presentation evidence, source-ordinal
tie-breaking, sub-millisecond positive intervals, and multi-cue round trips.
Focused subtitle tests (14), Ruff, strict Mypy, and the full suite (41) passed.
No FFmpeg, FFprobe, fixture, package, download, model, paid API, user media,
or CLI action occurred. Standards and specification review found no remaining
issues after the provenance and tie-breaker fixes.
