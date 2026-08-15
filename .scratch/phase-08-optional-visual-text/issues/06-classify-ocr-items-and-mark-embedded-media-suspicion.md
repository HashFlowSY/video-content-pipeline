# 06 — Classify OCR items and mark embedded-media suspicion

**What to build:** Versioned OCR-item classification rules and the Suspected
embedded-media interval marker — so that page text is distinguished from
speaker supplements and background UI, platform noise is auditable but never
formal evidence, and a possible embedded video is flagged at low confidence
with its evidential basis recorded rather than asserted as fact.

**Blocked by:** 05 — classification and suspicion operate on gated OCR
evidence items.

**Status:** ready-for-agent
**Labels:** ready-for-agent

- [ ] Versioned deterministic rules classify each OCR evidence item as page
  text, speaker supplement, or background UI; rule versions are recorded in
  provenance and the same input and versions always classify identically.
- [ ] Excluded visual items (danmaku, high-speed chat, unrelated watermarks,
  logos, follow/gift prompts, repeated platform shell) are retained in the
  workspace and marked non-evidence; they never appear as formal content.
- [ ] Low-confidence classifications are marked `classification_uncertain`
  and never forced into a category.
- [ ] Suspected embedded-media intervals are low-confidence markers only;
  provenance states whether the basis is picture-plus-audio or picture-only,
  and a supplied Audio analysis report is revalidated (hash and bound input
  identities) before its evidence is used.
- [ ] No clothing, environment, object, or action description and no visual
  summaries are produced anywhere.
