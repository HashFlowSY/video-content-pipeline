# 07 — Consume visual evidence in affected-Part re-analysis

**What to build:** The text-analysis side of the wiring: a retained
visual-text report becomes an Optional visual-text context input to a new
Affected-Part re-analysis attempt — so that chapters and summaries reflect
on-screen evidence without re-running unaffected Parts, and every visual fact
in a text report traces to retained OCR evidence.

**Blocked by:** 06 — the consumed report must carry classified evidence.

**Status:** ready-for-agent
**Labels:** ready-for-agent

- [ ] A retained visual-text report is loaded back to domain objects and
  revalidated (hash and bound input identities) before use; affected Parts
  are selected from the Parts carrying new visual evidence, and unaffected
  Parts are Carried-forward analysis Parts with provenance links.
- [ ] Page changes participate as candidate boundary evidence for
  Deterministically adjudicated semantic boundaries.
- [ ] Every OCR evidence item admitted to formal content is owned by exactly
  one formal SemanticSegment.
- [ ] Cited page facts appear only where classified page-text evidence
  exists; the absence of visual evidence never blocks subtitle-derived
  claims, and chapters and the collection summary are recomputed from the
  combined set.
- [ ] Re-analysis never overwrites prior reports and obeys all Phase 6
  contracts unchanged.
