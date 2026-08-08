# Separate raw, normalized, and presentation cues

Phase 2 represents subtitle evidence as immutable `RawCue`, `NormalizedCue`,
and `PresentationCue` layers. Normalization preserves every token, while only
the presentation layer may omit provably cumulative rolling-display tokens and
must retain token-level provenance; this keeps lossless evidence distinct from
the readable subtitle representation.

## Considered Options

- Separate immutable layers: accepted because it makes every transformation
  auditable and prevents de-duplication from corrupting source evidence.
- Mutate one cue representation in place: rejected because it conflates
  formatting, evidence, and display behavior.
