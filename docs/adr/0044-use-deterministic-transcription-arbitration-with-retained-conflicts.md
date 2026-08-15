# Use deterministic transcription arbitration with retained unresolved conflicts

When the primary ASR and the independent review ASR disagree on a suspicious
interval, versioned deterministic preference rules decide whether the review
candidate replaces the primary text. When no rule decides, no candidate is
chosen: the primary text stands, both candidates remain retained evidence, and
the interval is marked `review-needed`. The second model is independent
evidence and never automatically decides truth.

## Considered Options

- Deterministic rules with retained unresolved conflicts: accepted because it
  is reproducible, testable under strict TDD, and structurally identical to the
  Phase 6 deterministic boundary adjudication precedent.
- Text-model arbitration: rejected because it adds a third model's
  hallucination surface and is not reproducible.
- Confidence-weighted selection: rejected because confidence scores are not
  comparable across models and the result degenerates into the majority vote
  the plan explicitly forbids.
