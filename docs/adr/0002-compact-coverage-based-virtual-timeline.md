# Use a compact coverage-based virtual timeline

Phase 2 maps ordered Parts into contiguous collection virtual time from their
actual audio/video coverage envelopes: the first Part starts at zero and every
next Part starts at the preceding Part's half-open endpoint. Local PTS and time
base remain authoritative, while container durations and unrelated absolute PTS
gaps remain diagnostic only; this avoids artificial collection gaps without
erasing the hard Part boundary.

## Considered Options

- Compact coverage-based concatenation: accepted because it makes ordered
  Parts predictable despite different encoder PTS origins.
- Preserve container duration or absolute PTS gaps: rejected because those
  values can introduce gaps unrelated to the content being represented.
