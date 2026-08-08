# Use exact local proof for rolling subtitle de-duplication

Phase 2 removes rolling-display tokens only when stable-order adjacent cues in
the same Part and subtitle track have an exact normalized contiguous overlap,
the later cue strictly extends the earlier one, and their intervals overlap or
are contiguous. Fuzzy and semantic similarity never authorize removal; exact
full-text duplicates are removed only when their time ranges are also exactly
equal, preserving ambiguous repetition as `possible_duplicate`.

## Considered Options

- Exact local proof: accepted because it minimizes the risk of deleting real
  spoken repetition while remaining deterministic.
- Fuzzy or semantic matching: rejected because similarity cannot prove that a
  repeated phrase is only a platform display artifact.
