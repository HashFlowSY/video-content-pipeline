# 11 -- Prove fixture-backed integration behavior

Category: enhancement
Status: ready-for-agent
Labels: enhancement, ready-for-agent

**What to build:** Read-only integration proof that the retained synthetic
fixtures pass through raw probe evidence, typed projection, coverage,
coordinate mapping, and SRT/VTT behavior exactly as the Phase 2 contract
requires.

**Blocked by:** 10 -- Generate retained fixture evidence.

- [ ] Integration tests verify fixture hashes before interpreting retained
  media evidence and fail visibly on a missing or mismatched artifact.
- [ ] Tests cover probe projection, `StreamCoverage`, Part-relative and
  collection virtual mapping, and parseable SRT/VTT round trips.
- [ ] Tests never regenerate fixtures, access user media, use network state, or
  delete fixture, cache, or failed-output evidence.
