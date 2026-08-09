# 01 -- Local source preflight report

**What to build:** A user can submit one explicit regular local file and
receive a retained PlanReport that either proves a double-hashed SourceArtifact
with deterministic disk headroom or explains why acquisition was blocked.

**Blocked by:** None -- can start immediately.

**Status:** resolved

- [x] The command accepts only an explicit regular local file and leaves the
  original bytes untouched.
- [x] A valid input becomes one content-addressed SourceArtifact only after
  matching pre-copy and post-copy hashes.
- [x] Duplicate content reuses a SourceArtifact; changed or non-regular input
  produces a retained blocked PlanReport.
- [x] Disk headroom is checked before copy using the adopted reserve rule.

## Comments

2026-08-09: Implemented the local source preflight boundary. Regular local
sources are copied into content-addressed SourceArtifacts only after matching
pre- and post-copy hashes, with duplicate reuse and deterministic disk
headroom. Non-regular input, source changes, and insufficient disk headroom
now retain a blocked PlanReport with a structured diagnostic. Offline unit
tests, linting, formatting, and strict type checks pass.
