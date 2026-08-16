# 08 — Implement staging, atomic publish, manifest, and the latest pointer

**What to build:** The publication mechanism (ADR 0051): candidate artifacts
assembled with recorded hashes in `work/<source-id>/<run-id>/staging/` in
final RunBundle layout; an `st_dev` same-filesystem precheck (mismatch
errors, never silent copying); publication as one whole-directory rename to
`outputs/<source-id>/<run-id>/`; post-publish re-hash of every file against
the RunBundle manifest; and the per-source Latest pointer with its
eligibility rule.

**Blocked by:** 01, 07

**Status:** done
**Labels:** ready-for-agent

- [x] The manifest lists every expected artifact with status
  `valid | partial | invalid | unavailable` and hash; manifest ↔ disk match
  is bidirectional (nothing extra, nothing missing).
- [x] Publication is a single atomic rename; a failure before the rename
  leaves `outputs/` without any trace of the run; a failure after is
  detected by the post-publish re-hash.
- [x] The `st_dev` precheck runs before staging assembly completes its
  contract; cross-device conditions error without degradation.
- [x] An existing `outputs/<source-id>/<run-id>/` is never overwritten.
- [x] `latest.json` advances only for `complete`, `complete_with_warnings`,
  or published-partial runs; purely failed runs never advance it; it stores
  a pointer, never copies.
- [x] Post-publish verification failures are journaled and reported, and the
  bundle is marked accordingly — never silently accepted.

## Comments

Implemented in commit d4191d0 feat: implement staging, atomic publish,
manifest, and latest pointer (Phase 9 ticket 08). Acceptance criteria were
checked at phase closure on the maintainer's instruction, anchored to the
current-head verification (pytest 1034 passed; ruff and mypy clean; 21
confirmed exit-gate booleans in docs/PHASE_09_INVENTORY.json).
