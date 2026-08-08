# Parse FFprobe JSON without fallback guessing

Phase 2 retains FFprobe's raw JSON as `ProbeDocument` and derives a typed
`ProbeProjection` for decisions. Unknown fields are tolerated but ignored by
the projection; missing or invalid required values create structured
`probe_invalid` and, when applicable, `coverage_indeterminate` diagnostics, with
no fallback to human-readable text, regular expressions, or duration guesses.

## Considered Options

- Typed JSON projection with explicit diagnostics: accepted because it is
  forward-compatible without inventing unavailable evidence.
- Best-effort text or metadata fallback: rejected because it can silently
  produce an incorrect canonical timeline.
