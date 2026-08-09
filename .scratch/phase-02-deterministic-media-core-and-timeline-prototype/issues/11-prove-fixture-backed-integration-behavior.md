# 11 -- Prove fixture-backed integration behavior

Category: enhancement
Status: resolved
Labels: enhancement

**What to build:** Read-only integration proof that the retained synthetic
fixtures pass through raw probe evidence, typed projection, coverage,
coordinate mapping, and SRT/VTT behavior exactly as the Phase 2 contract
requires.

**Blocked by:** 10 -- Generate retained fixture evidence.

- [x] Integration tests verify fixture hashes before interpreting retained
  media evidence and fail visibly on a missing or mismatched artifact.
- [x] Tests cover probe projection, `StreamCoverage`, Part-relative and
  collection virtual mapping, and parseable SRT/VTT round trips.
- [x] Tests never regenerate fixtures, access user media, use network state, or
  delete fixture, cache, or failed-output evidence.

## Comments

2026-08-09: Added read-only fixture-backed integration coverage. Every test
verifies all 12 canonical manifest entries for relative path, byte count, and
SHA-256 before reading ProbeDocuments or subtitle evidence; missing and
mismatched artifacts fail with explicit assertions. The retained frame evidence
proves `gap-video` coverage `[10, 13.9)` and its internal gap `[11, 13)`; the
previous `[13, 14)` expectation remains in the approved fixture proposal and
Ticket 10 ledger. Ticket 11 records this discrepancy but does not amend that
approved contract or change fixture bytes.
The follow-up review fixed two gaps: the manifest now requires the exact
canonical entry set, required retention and fixture metadata, matching media
and probe tool provenance, and no unexpected retained files beyond the
non-self-hashable manifest and archived invalid-manifest evidence. SRT/VTT
serialization also retains source cue identifiers.
The tests verify the retained VTT identifier, setting, multiline text, and
one-millisecond interval across both round trips. The suite passed (`48
passed`), as did ruff, format, and `mypy src`. No fixture generation, deletion,
FFmpeg, FFprobe, network access, user media, model, paid service, or
media-facing CLI action occurred.
