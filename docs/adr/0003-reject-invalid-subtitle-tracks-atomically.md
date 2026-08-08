# Reject invalid subtitle tracks atomically

Phase 2 treats each subtitle track as one evidence candidate: raw input is
retained, and lossless normalization or output is allowed only after every cue
parses and passes ordering, duration, and media-coverage validation. A single
failure marks the whole track `invalid` with structured diagnostics rather than
silently repairing it or publishing a partial recovery, preserving explicit
evidence gaps over deceptive completeness.

## Considered Options

- Atomic rejection: accepted because a partial subtitle stream can hide missing
  evidence and make downstream output look complete when it is not.
- Recover valid cues: rejected because it makes the completeness boundary
  ambiguous and risks silently omitting content.
