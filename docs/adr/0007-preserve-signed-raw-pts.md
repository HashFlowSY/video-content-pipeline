# Preserve signed raw PTS

Phase 2 preserves the signed integer PTS reported by each stream, including
negative values, as source evidence. Actual coverage is computed from those
exact values; only collection virtual time translates a Part's coverage origin
to zero, avoiding loss of edit-list or decoder pre-roll information.

## Considered Options

- Preserve signed PTS and translate only virtual time: accepted because it
  retains stream truth while presenting a usable collection coordinate system.
- Clamp negative PTS to zero: rejected because it changes coverage and hides
  meaningful media timing evidence.
