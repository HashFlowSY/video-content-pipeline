# 07 — Consume visual evidence in affected-Part re-analysis

**What to build:** The text-analysis side of the wiring: a retained
visual-text report becomes an Optional visual-text context input to a new
Affected-Part re-analysis attempt — so that chapters and summaries reflect
on-screen evidence without re-running unaffected Parts, and every visual fact
in a text report traces to retained OCR evidence.

**Blocked by:** 06 — the consumed report must carry classified evidence.

**Status:** done
**Labels:** ready-for-agent

- [x] A retained visual-text report is loaded back to domain objects and
  revalidated (hash and bound input identities) before use; affected Parts
  are selected from the Parts carrying new visual evidence, and unaffected
  Parts are Carried-forward analysis Parts with provenance links.
- [x] Page changes participate as candidate boundary evidence for
  Deterministically adjudicated semantic boundaries.
- [x] Every OCR evidence item admitted to formal content is owned by exactly
  one formal SemanticSegment.
- [x] Cited page facts appear only where classified page-text evidence
  exists; the absence of visual evidence never blocks subtitle-derived
  claims, and chapters and the collection summary are recomputed from the
  combined set.
- [x] Re-analysis never overwrites prior reports and obeys all Phase 6
  contracts unchanged.

## Comments

Implemented in commit 15f9673 feat: consume visual evidence in affected-Part re-analysis. Acceptance criteria were checked at phase
closure on the maintainer's instruction, anchored to the current-head
verification (pytest 700 passed; ruff and mypy clean; 30 confirmed exit-gate
booleans in docs/PHASE_08_INVENTORY.json).
