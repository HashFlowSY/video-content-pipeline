# Separate source, Part-relative, and collection time

Phase 2 uses three exact rational coordinate systems: signed `RawPtsTime` for
source evidence, non-negative `PartRelativeTime` for per-Part subtitle export,
and contiguous `CollectionVirtualTime` for ordered collections. Each mapping is
only a translation from the Part coverage start or preceding coverage span, so
the export coordinates remain usable without losing the ability to recover the
authoritative raw PTS.

## Considered Options

- Separate the three coordinates: accepted because negative PTS makes one
  ambiguous part-local coordinate unsafe for both evidence and export.
- Reuse one `part_local_time` coordinate: rejected because it obscures origin,
  permits invalid negative subtitle times, and makes provenance unclear.
