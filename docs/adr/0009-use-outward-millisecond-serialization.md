# Use outward millisecond subtitle serialization

Phase 2 serializes exact rational subtitle intervals with a floored start and
ceiled end in milliseconds. The resulting SRT/VTT serialization envelope may
extend outward by less than one millisecond at either endpoint, while the exact
range and source PTS remain authoritative; this preserves every positive source
interval without using floating-point accumulation.

## Considered Options

- Floor starts and ceil ends: accepted because it avoids truncating subtitle
  evidence and guarantees a positive millisecond interval for positive input.
- Round inward or to nearest: rejected because it can lose evidence or make a
  positive exact interval disappear during serialization.
