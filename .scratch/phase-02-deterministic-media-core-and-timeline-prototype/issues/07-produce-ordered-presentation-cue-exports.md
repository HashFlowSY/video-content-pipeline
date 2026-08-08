# 07 -- Produce ordered PresentationCue exports

Category: enhancement
Status: ready-for-agent
Labels: enhancement, ready-for-agent

**What to build:** A deterministic presentation and export path for accepted
subtitle evidence that preserves valid cue overlap, exposes stable order, and
serializes exact time outward to parseable millisecond SRT and VTT intervals.

**Blocked by:** 05 -- Assemble compact CollectionVirtualTime; 06 -- Accept atomic subtitle tracks.

- [ ] `PresentationCue` remains separately immutable from `RawCue` and
  `NormalizedCue`, with no source-text mutation in the export path.
- [ ] Cue order is `(start, end, source_ordinal)` and overlapping intervals
  remain valid evidence rather than being trimmed, merged, or shifted.
- [ ] Export floors exact starts and ceils exact ends, including positive
  sub-millisecond source intervals, while retaining the serialization envelope
  as derived rather than authoritative time.
