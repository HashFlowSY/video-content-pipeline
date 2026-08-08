# Retain hash-pinned synthetic media fixtures

Phase 2 creates deterministic synthetic media only through explicitly
announced, versioned fixture recipes and retains the generated assets under
`tests/fixtures/` with their FFmpeg provenance, hashes, and expected
`ProbeDocument` values. Normal tests read those fixed assets and do not
regenerate or delete them, keeping integration results reproducible without
touching user media.

## Considered Options

- Retain hash-pinned fixtures: accepted because it separates fixture creation
  from ordinary testing and preserves an audit trail for generated media.
- Generate fixtures on every test run: rejected because it makes tests depend
  on runtime FFmpeg behavior and conflicts with the no-automatic-deletion rule.
