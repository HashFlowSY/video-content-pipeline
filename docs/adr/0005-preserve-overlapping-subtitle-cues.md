# Preserve overlapping subtitle cues

Phase 2 treats overlap as valid subtitle evidence. Cues are ordered
deterministically by `(start, end, source_ordinal)`, while their intervals may
overlap; the pipeline must not trim, merge, or shift them merely to make the
track non-overlapping. This preserves simultaneous speech and source timing
without sacrificing deterministic output order.

## Considered Options

- Preserve overlap with a stable ordering key: accepted because valid
  concurrent speech is a required Phase 2 test case.
- Enforce non-overlap by rewriting intervals: rejected because it destroys
  source evidence and conflates ordering with exclusivity.
